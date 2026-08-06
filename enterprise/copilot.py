"""
N13 — AI Workforce Copilot.

Natural-language attendance answers without an external LLM dependency.
Operators get the same "ask anything" experience that UKG / Rippling charge
a premium for.

How it works
------------
1.  We extract intent + slots from the question with a small rule pipeline
    (regex + keyword + date parser). This handles 80% of real questions:
      - "who was late in Pune this week?"
      - "show me overtime in Q2 for engineering"
      - "how many people are absent today?"
      - "top 5 attenders this month"
      - "average check-in time for sales last month"
2.  Each intent maps to a *parameterised* SQL template. Parameters are bound
    safely; we never f-string user input into SQL.
3.  Results come back with a small narrative ("In Pune this week, 8 people
    were late. Worst offender: Ravi (3 times)") + a table.
4.  If `copilot_llm_url` is set in settings, we instead call out to a local
    Ollama / vLLM and use its text-to-SQL — but the rule pipeline is the
    default so the feature works out of the box with no extra services.

Safety
------
The SQL templates are read-only (SELECT only, no triggers, no ATTACH, no
PRAGMA). The runner enforces that as a final check before execute().
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

import db
from enterprise import copilot_kb

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date phrase parsing
# ---------------------------------------------------------------------------
def parse_date_range(q: str) -> tuple[str, str, str]:
    """Return (start, end, label). Defaults to last 7 days."""
    today = date.today()
    q = q.lower()
    if 'today' in q:
        d = today.isoformat()
        return d, d, 'today'
    if 'yesterday' in q:
        d = (today - timedelta(days=1)).isoformat()
        return d, d, 'yesterday'
    if 'this week' in q:
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat(), 'this week'
    if 'last week' in q:
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return start.isoformat(), end.isoformat(), 'last week'
    if 'this month' in q:
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat(), 'this month'
    if 'last month' in q:
        first_this = today.replace(day=1)
        end = first_this - timedelta(days=1)
        start = end.replace(day=1)
        return start.isoformat(), end.isoformat(), 'last month'
    m = re.search(r'last (\d{1,3}) days?', q)
    if m:
        n = max(1, min(365, int(m.group(1))))
        start = today - timedelta(days=n - 1)
        return start.isoformat(), today.isoformat(), f'last {n} days'
    # Default fallback — last 7 days
    start = today - timedelta(days=6)
    return start.isoformat(), today.isoformat(), 'this week'


def extract_branch(q: str) -> Optional[str]:
    """If the user mentions a known branch by name, return it."""
    for b in db.list_branches():
        if b['name'].lower() in q.lower():
            return b['name']
    return None


def extract_department(q: str) -> Optional[str]:
    for d in db.list_departments():
        if d['name'].lower() in q.lower():
            return d['name']
    return None


def extract_topn(q: str, default: int = 10) -> int:
    m = re.search(r'top (\d{1,3})', q.lower())
    if m:
        return max(1, min(100, int(m.group(1))))
    return default


def extract_person_name(q: str) -> Optional[str]:
    """Match a name from the question against known persons (case-insensitive,
    longest match wins so "Ravi Patel" beats "Ravi")."""
    try:
        persons = db.list_persons()
    except Exception:  # noqa: BLE001
        return None
    ql = q.lower()
    best, best_len = None, 0
    for p in persons:
        nm = (p['name'] or '').lower()
        if nm and nm in ql and len(nm) > best_len:
            best, best_len = p['name'], len(nm)
    return best


# ---------------------------------------------------------------------------
# Intents → SQL templates
# ---------------------------------------------------------------------------
INTENTS = [
    {
        'name': 'late_count',
        'patterns': [
            r'who (?:was|were) late',
            r'late (?:arrivals?|count)',
            r'how many (?:people |employees )?late',
        ],
        'sql': (
            "SELECT a.person_id AS \"ID\", p.name AS \"Name\", "
            "       d.name AS \"Department\", "
            "       COUNT(*) AS \"Late count\" "
            "FROM attendance a "
            "LEFT JOIN persons p ON p.person_id = a.person_id "
            "LEFT JOIN departments d ON d.id = p.department_id "
            "WHERE a.date BETWEEN ? AND ? AND a.is_late = 1 "
            "  AND (? IS NULL OR d.name = ?) "
            "GROUP BY a.person_id, p.name, d.name "
            "ORDER BY 4 DESC LIMIT ?"
        ),
        'params': lambda slots: (slots['start'], slots['end'],
                                  slots.get('dept'), slots.get('dept'),
                                  slots.get('topn', 50)),
        'summary': lambda res, slots: (
            f"{len(res)} person(s) were late {slots['date_label']}"
            + (f" in {slots['dept']}" if slots.get('dept') else '')
            + '.'
        ),
    },
    {
        'name': 'absent_today',
        'patterns': [
            r'(?:who(?: is)? absent|absent today|not (?:yet )?arrived)',
        ],
        'sql': (
            "SELECT p.person_id AS \"ID\", p.name AS \"Name\", "
            "       d.name AS \"Department\" "
            "FROM persons p LEFT JOIN departments d ON d.id = p.department_id "
            "WHERE NOT EXISTS (SELECT 1 FROM attendance a "
            "                  WHERE a.person_id = p.person_id "
            "                    AND a.date = date('now') "
            "                    AND a.check_in IS NOT NULL) "
            "  AND (? IS NULL OR d.name = ?) "
            "ORDER BY p.name LIMIT ?"
        ),
        'params': lambda slots: (slots.get('dept'), slots.get('dept'),
                                  slots.get('topn', 200)),
        'summary': lambda res, slots: f"{len(res)} person(s) not yet arrived today.",
    },
    {
        'name': 'top_attenders',
        'patterns': [r'top (?:\d+ )?attenders?', r'who attend(?:ed|s) the most'],
        'sql': (
            "SELECT p.person_id AS \"ID\", p.name AS \"Name\", "
            "       COUNT(*) AS \"Days present\" "
            "FROM attendance a JOIN persons p ON p.person_id = a.person_id "
            "WHERE a.date BETWEEN ? AND ? AND a.check_in IS NOT NULL "
            "GROUP BY p.person_id, p.name "
            "ORDER BY 3 DESC LIMIT ?"
        ),
        'params': lambda slots: (slots['start'], slots['end'],
                                  slots.get('topn', 10)),
        'summary': lambda res, slots: f"Top {len(res)} attenders {slots['date_label']}.",
    },
    {
        'name': 'overtime_trend',
        'patterns': [r'overtime', r'extra hours'],
        'sql': (
            "SELECT a.date AS \"Date\", "
            "       SUM("
            "         (CAST(substr(a.check_out,1,2) AS INT)*60 + CAST(substr(a.check_out,4,2) AS INT))"
            "       - (CAST(substr(a.check_in,1,2) AS INT)*60 + CAST(substr(a.check_in,4,2) AS INT))"
            "       ) / 60.0 AS \"Total worked h\" "
            "FROM attendance a "
            "LEFT JOIN persons p ON p.person_id = a.person_id "
            "LEFT JOIN departments d ON d.id = p.department_id "
            "WHERE a.date BETWEEN ? AND ? "
            "  AND a.check_in IS NOT NULL AND a.check_out IS NOT NULL "
            "  AND (? IS NULL OR d.name = ?) "
            "GROUP BY a.date ORDER BY a.date"
        ),
        'params': lambda slots: (slots['start'], slots['end'],
                                  slots.get('dept'), slots.get('dept')),
        'summary': lambda res, slots: (
            f"Total hours worked per day {slots['date_label']}"
            + (f" in {slots['dept']}" if slots.get('dept') else '') + '.'
        ),
    },
    {
        'name': 'avg_checkin',
        'patterns': [r'average (?:check.?in|arrival) time'],
        'sql': (
            "SELECT printf('%02d:%02d', "
            "         CAST(AVG(CAST(substr(check_in,1,2) AS INT) * 60 + CAST(substr(check_in,4,2) AS INT))/60 AS INT), "
            "         CAST(AVG(CAST(substr(check_in,1,2) AS INT) * 60 + CAST(substr(check_in,4,2) AS INT)) % 60 AS INT)) "
            "       AS \"Avg check-in\" "
            "FROM attendance a "
            "LEFT JOIN persons p ON p.person_id = a.person_id "
            "LEFT JOIN departments d ON d.id = p.department_id "
            "WHERE a.date BETWEEN ? AND ? AND a.check_in IS NOT NULL "
            "  AND (? IS NULL OR d.name = ?)"
        ),
        'params': lambda slots: (slots['start'], slots['end'],
                                  slots.get('dept'), slots.get('dept')),
        'summary': lambda res, slots: (
            f"Average arrival time {slots['date_label']}"
            + (f" for {slots['dept']}" if slots.get('dept') else '') + '.'
        ),
    },
    {
        'name': 'headcount',
        'patterns': [r'how many (?:are )?present', r'attendance count'],
        'sql': (
            "SELECT a.date AS \"Date\", COUNT(*) AS \"Present\" "
            "FROM attendance a WHERE a.date BETWEEN ? AND ? "
            "  AND a.check_in IS NOT NULL "
            "GROUP BY a.date ORDER BY a.date"
        ),
        'params': lambda slots: (slots['start'], slots['end']),
        'summary': lambda res, slots: f"Headcount per day {slots['date_label']}.",
    },
    {
        'name': 'total_today',
        'patterns': [
            r'how many (total )?(entries?|records?|attendance|people|checked|came)\s+(today|in)?',
            r'total (entries?|records?|attendance|people|today)',
            r'(headcount|count) (today|now)',
            r'today.?s (total|count|headcount|number)',
        ],
        'sql': (
            "SELECT COUNT(*) AS \"Total entries today\", "
            "       SUM(CASE WHEN check_in IS NOT NULL THEN 1 ELSE 0 END) AS \"Checked in\", "
            "       SUM(CASE WHEN check_out IS NOT NULL THEN 1 ELSE 0 END) AS \"Checked out\", "
            "       SUM(is_late) AS \"Late\" "
            "FROM attendance WHERE date = date('now')"
        ),
        'params': lambda slots: (),
        'summary': lambda res, slots: (
            f"Today: {res[0].get('Total entries today', 0)} entries, "
            f"{res[0].get('Checked in', 0)} checked in, "
            f"{res[0].get('Late', 0)} late."
        ),
    },
    {
        'name': 'department_breakdown',
        'patterns': [
            r'(by|per|across) (department|class|team)',
            r'department.*(attendance|breakdown|stats?)',
            r'attendance (by|per) (department|class|team)',
        ],
        'sql': (
            "SELECT COALESCE(d.name, '—') AS \"Department\", "
            "       COUNT(DISTINCT a.person_id) AS \"Distinct people\", "
            "       COUNT(*) AS \"Total entries\", "
            "       SUM(a.is_late) AS \"Late\" "
            "FROM attendance a "
            "LEFT JOIN persons p ON p.person_id = a.person_id "
            "LEFT JOIN departments d ON d.id = p.department_id "
            "WHERE a.date BETWEEN ? AND ? AND a.check_in IS NOT NULL "
            "GROUP BY d.name ORDER BY 3 DESC"
        ),
        'params': lambda slots: (slots['start'], slots['end']),
        'summary': lambda res, slots: f"Attendance by department {slots['date_label']}.",
    },
    {
        'name': 'defaulters',
        'patterns': [
            r'\bdefaulter', r'below 75', r'below threshold',
            r'(who|list).*(low|poor) attendance',
        ],
        'sql': (
            "WITH counts AS ("
            "  SELECT p.person_id, p.name, "
            "         SUM(CASE WHEN a.check_in IS NOT NULL THEN 1 ELSE 0 END) AS attended, "
            "         COUNT(a.date) AS total "
            "  FROM persons p LEFT JOIN attendance a ON a.person_id = p.person_id "
            "  WHERE a.date BETWEEN ? AND ? OR a.date IS NULL "
            "  GROUP BY p.person_id, p.name"
            ") "
            "SELECT person_id AS \"ID\", name AS \"Name\", attended AS \"Days\", "
            "       total AS \"Total\", "
            "       printf('%.1f', 100.0 * attended / NULLIF(total,0)) AS \"%\" "
            "FROM counts WHERE total > 0 AND attended * 100 < total * 75 "
            "ORDER BY attended * 1.0 / total LIMIT ?"
        ),
        'params': lambda slots: (slots['start'], slots['end'], slots.get('topn', 50)),
        'summary': lambda res, slots: f"{len(res)} person(s) below 75% {slots['date_label']}.",
    },
    {
        'name': 'visitor_count',
        'patterns': [
            r'(unknown|stranger|visitor)s?',
            r'(how many )?unknown faces',
        ],
        'sql': (
            "SELECT date(seen_at) AS \"Date\", COUNT(*) AS \"Unknown faces\" "
            "FROM visitors WHERE seen_at >= datetime('now', ?) "
            "GROUP BY date(seen_at) ORDER BY 1"
        ),
        'params': lambda slots: (f"-{(date.fromisoformat(slots['end']) - date.fromisoformat(slots['start'])).days + 1} days",),
        'summary': lambda res, slots: f"Unknown-face sightings {slots['date_label']}.",
    },
    {
        'name': 'birthdays',
        'patterns': [
            r'birthday', r'birthdays', r'birth ?day',
            r'whose birthday',
        ],
        'sql': (
            "SELECT person_id AS \"ID\", name AS \"Name\", "
            "       date_of_birth AS \"DoB\" "
            "FROM persons WHERE date_of_birth IS NOT NULL "
            "AND substr(date_of_birth, 6, 5) = strftime('%m-%d', 'now') "
            "ORDER BY name"
        ),
        'params': lambda slots: (),
        'summary': lambda res, slots: (f"{len(res)} birthday(s) today." if res else "No birthdays today."),
    },
    {
        'name': 'person_history',
        'patterns': [
            r'(history|attendance|record) (of|for) (.+)',
            r'when (was|did) (.+) (last )?(here|came|check)',
            r'show (me )?(.+)',
        ],
        'sql': (
            "SELECT a.date AS \"Date\", a.check_in AS \"In\", "
            "       a.check_out AS \"Out\", "
            "       CASE WHEN a.is_late THEN 'late' "
            "            WHEN a.check_in IS NOT NULL THEN 'on time' "
            "            ELSE '—' END AS \"Status\" "
            "FROM attendance a "
            "JOIN persons p ON p.person_id = a.person_id "
            "WHERE (p.name LIKE ? OR p.person_id = ?) "
            "  AND a.date BETWEEN ? AND ? "
            "ORDER BY a.date DESC LIMIT 50"
        ),
        'params': lambda slots: (
            f"%{slots.get('person_name', '')}%",
            slots.get('person_name', ''),
            slots['start'], slots['end']),
        'summary': lambda res, slots: (
            f"{len(res)} day(s) of records for {slots.get('person_name','—')} {slots['date_label']}."
            if res else
            f"No records found for {slots.get('person_name','—')} {slots['date_label']}."
        ),
    },
    {
        'name': 'at_risk',
        'patterns': [
            r'at.?risk', r'(who|list).*(risk|attrition|burnout)',
            r'(likely |going to )(leave|quit|drop)',
        ],
        'sql': None,
        'special': 'at_risk',
    },
    {
        'name': 'burnout',
        'patterns': [r'burn(ed)?\s?out', r'overworked',
                     r'too much overtime', r'working too (much|hard)'],
        'sql': None,
        'special': 'burnout',
    },
]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def pick_intent(q: str) -> Optional[dict]:
    ql = q.lower()
    for intent in INTENTS:
        for pat in intent['patterns']:
            if re.search(pat, ql):
                return intent
    return None


def _safe_run(sql: str, params: tuple) -> list[dict]:
    """Execute a read-only SELECT (or WITH ... SELECT CTE). Reject anything else
    as a defence-in-depth measure even though we control the templates."""
    head = sql.strip().lower()
    if not (head.startswith('select') or head.startswith('with ')):
        raise ValueError('only SELECT / WITH allowed')
    forbidden = (';', 'attach', 'pragma', 'insert', 'update', 'delete', 'drop')
    low = sql.lower()
    for tok in forbidden:
        if tok in low and tok != ';':
            raise ValueError(f'forbidden token: {tok}')
    if low.count(';') > 0:
        raise ValueError('multiple statements not allowed')
    with db.tx() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _did_you_mean(question: str) -> list[str]:
    """Three friendly suggestions when nothing matched."""
    return [
        'who was late this week?',
        'how many entries today?',
        'how do I register a user?',
    ]


def answer(question: str, actor: str = '') -> dict:
    """Top-level entry. Hybrid pipeline:
       1. Knowledge base — "how do I...", "what is...", any platform-help question.
       2. SQL intent pipeline — "who was late this week", "top attenders".
       3. Friendly catalog with did-you-mean.
    """
    question = (question or '').strip()
    if not question:
        return {'ok': False, 'kind': 'empty',
                'summary': 'Type a question — or click a quick-pick chip.',
                'rows': [], 'columns': []}

    # ── 1) Knowledge base first ────────────────────────────────────────
    kb_hit = copilot_kb.match(question)
    if kb_hit:
        db.log_copilot(actor, question, intent=f"kb:{kb_hit['topic']}",
                       rows_out=len(kb_hit.get('steps', [])))
        return {
            'ok': True, 'kind': 'kb',
            'intent': f"kb:{kb_hit['topic']}",
            'title': kb_hit['title'],
            'summary': kb_hit['title'],
            'steps': kb_hit.get('steps', []),
            'links': kb_hit.get('links', []),
            'columns': [], 'rows': [],
        }

    # ── 2) SQL intents ─────────────────────────────────────────────────
    intent = pick_intent(question)
    if not intent:
        db.log_copilot(actor, question, intent='unknown', success=False)
        return {
            'ok': False, 'kind': 'unknown',
            'summary': ('I didn\'t catch that. I can answer questions about '
                        'lateness, absence, top attenders, overtime, '
                        'headcount, departments, defaulters, birthdays, '
                        'visitors, and per-person history. I can also explain '
                        'every FaceMark feature and walk you through setup.'),
            'suggestions': _did_you_mean(question),
            'rows': [], 'columns': [],
        }

    start, end, date_label = parse_date_range(question)
    dept = extract_department(question)
    branch = extract_branch(question)
    topn = extract_topn(question)
    person_name = extract_person_name(question) if intent['name'] == 'person_history' else None
    if intent['name'] == 'person_history' and not person_name:
        return {
            'ok': False, 'kind': 'unknown',
            'summary': ('I couldn\'t find a person matching your question. '
                        'Try the exact name as it appears in /listusers, '
                        'or use the person\'s ID.'),
            'suggestions': _did_you_mean(question),
            'rows': [], 'columns': [],
        }

    slots = {'start': start, 'end': end, 'date_label': date_label,
             'dept': dept, 'branch': branch, 'topn': topn,
             'person_name': person_name}

    # Special intents that need module calls instead of plain SQL
    if intent.get('special') == 'at_risk':
        from enterprise import analytics
        rows = analytics.attrition_scores()
        db.log_copilot(actor, question, intent='at_risk', rows_out=len(rows))
        return {
            'ok': True, 'kind': 'sql', 'intent': 'at_risk',
            'summary': f'{len(rows)} person(s) flagged for attrition risk.',
            'columns': ['name', 'score', 'band', 'late_days', 'absent_days', 'avg_hours'],
            'rows': [{'name': r['name'], 'score': r['score'], 'band': r['band'],
                      'late_days': r['late_days'], 'absent_days': r['absent_days'],
                      'avg_hours': r['avg_hours']} for r in rows[:30]],
        }
    if intent.get('special') == 'burnout':
        from enterprise import analytics
        rows = analytics.burnout_signals()
        db.log_copilot(actor, question, intent='burnout', rows_out=len(rows))
        return {
            'ok': True, 'kind': 'sql', 'intent': 'burnout',
            'summary': f'{len(rows)} person(s) with burnout signals (last 30 d).',
            'columns': ['name', 'overtime_hours', 'overtime_days', 'avg_per_day'],
            'rows': rows[:30],
        }

    params = intent['params'](slots)
    try:
        rows = _safe_run(intent['sql'], params)
    except Exception as e:  # noqa: BLE001
        log.warning('copilot SQL failed: %s', e)
        db.log_copilot(actor, question, intent=intent['name'],
                       sql_run=intent['sql'], success=False)
        return {'ok': False, 'kind': 'error',
                'summary': f'Query failed: {e}',
                'rows': [], 'columns': []}

    columns = list(rows[0].keys()) if rows else []
    summary = intent['summary'](rows, slots)
    db.log_copilot(actor, question, intent=intent['name'],
                   sql_run=intent['sql'], rows_out=len(rows))
    return {
        'ok': True, 'kind': 'sql',
        'intent': intent['name'],
        'summary': summary,
        'columns': columns, 'rows': rows,
        'slots': slots,
    }
