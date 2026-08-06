"""
Tiny i18n for FaceMark — Hindi + English at launch.

Public:
    T(key, lang=None)    Translate a key. Falls back to the key itself.
    languages()          List of supported languages.
    detect(request)      Read preferred language from cookie / Accept-Language.
"""

from __future__ import annotations

from typing import Optional


SUPPORTED = ('en', 'hi', 'mr', 'gu', 'ta', 'kn')

DICT = {
    # —— Navigation / shell
    'app_name':          {'hi': 'फेसमार्क', 'mr': 'फेसमार्क', 'gu': 'ફેસમાર્ક',
                          'ta': 'பேஸ்மார்க்', 'kn': 'ಫೇಸ್‌ಮಾರ್ಕ್'},
    'dashboard':         {'hi': 'डैशबोर्ड', 'mr': 'डॅशबोर्ड', 'gu': 'ડેશબોર્ડ',
                          'ta': 'டாஷ்போர்ட்', 'kn': 'ಡ್ಯಾಶ್‌ಬೋರ್ಡ್'},
    'users':             {'hi': 'उपयोगकर्ता', 'mr': 'वापरकर्ते', 'gu': 'વપરાશકર્તાઓ',
                          'ta': 'பயனாளர்கள்', 'kn': 'ಬಳಕೆದಾರರು'},
    'history':           {'hi': 'इतिहास', 'mr': 'इतिहास', 'gu': 'ઇતિહાસ',
                          'ta': 'வரலாறு', 'kn': 'ಇತಿಹಾಸ'},
    'analytics':         {'hi': 'विश्लेषण', 'mr': 'विश्लेषण', 'gu': 'વિશ્લેષણ',
                          'ta': 'பகுப்பாய்வு', 'kn': 'ವಿಶ್ಲೇಷಣೆ'},
    'settings':          {'hi': 'सेटिंग्स', 'mr': 'सेटिंग्ज', 'gu': 'સેટિંગ્સ',
                          'ta': 'அமைப்புகள்', 'kn': 'ಸೆಟ್ಟಿಂಗ್‌ಗಳು'},
    'sessions':          {'hi': 'सत्र', 'mr': 'सत्रे', 'gu': 'સત્રો',
                          'ta': 'அமர்வுகள்', 'kn': 'ಅಧಿವೇಶನಗಳು'},
    'logout':            {'hi': 'लॉग आउट', 'mr': 'लॉग आउट', 'gu': 'લોગ આઉટ',
                          'ta': 'வெளியேறு', 'kn': 'ಲಾಗ್ ಔಟ್'},
    'mark_attendance':   {'hi': 'उपस्थिति दर्ज करें', 'mr': 'उपस्थिती नोंदवा',
                          'gu': 'હાજરી નોંધો', 'ta': 'வருகை பதிவு',
                          'kn': 'ಹಾಜರಾತಿ ದಾಖಲಿಸಿ'},
    'check_in':          {'hi': 'अंदर', 'mr': 'आत', 'gu': 'અંદર',
                          'ta': 'உள்ளே', 'kn': 'ಒಳಗೆ'},
    'check_out':         {'hi': 'बाहर', 'mr': 'बाहेर', 'gu': 'બહાર',
                          'ta': 'வெளியே', 'kn': 'ಹೊರಗೆ'},
    'present':           {'hi': 'उपस्थित', 'mr': 'उपस्थित', 'gu': 'હાજર',
                          'ta': 'வந்துள்ளவர்', 'kn': 'ಹಾಜರಿದ್ದಾರೆ'},
    'absent':            {'hi': 'अनुपस्थित', 'mr': 'अनुपस्थित', 'gu': 'ગેરહાજર',
                          'ta': 'வரவில்லை', 'kn': 'ಗೈರು'},
    'late':              {'hi': 'देर से', 'mr': 'उशिरा', 'gu': 'મોડું',
                          'ta': 'தாமதம்', 'kn': 'ತಡ'},
    'submit':            {'hi': 'जमा करें', 'mr': 'सबमिट', 'gu': 'સબમિટ',
                          'ta': 'சமர்ப்பி', 'kn': 'ಸಲ್ಲಿಸಿ'},
    'leaves':            {'hi': 'अवकाश', 'mr': 'रजा', 'gu': 'રજાઓ',
                          'ta': 'விடுப்பு', 'kn': 'ರಜೆ'},
    'sites':             {'hi': 'स्थान', 'mr': 'साइट', 'gu': 'સાઇટ્સ',
                          'ta': 'தளங்கள்', 'kn': 'ಸೈಟ್‌ಗಳು'},
    'consent':           {'hi': 'सहमति', 'mr': 'सम्मती', 'gu': 'સંમતિ',
                          'ta': 'ஒப்புதல்', 'kn': 'ಸಮ್ಮತಿ'},
    'pin':               {'hi': 'पिन', 'mr': 'पिन', 'gu': 'પિન',
                          'ta': 'பின்', 'kn': 'ಪಿನ್'},
}


def languages() -> list[tuple[str, str]]:
    return [
        ('en', 'English'),
        ('hi', 'हिन्दी'),
        ('mr', 'मराठी'),
        ('gu', 'ગુજરાતી'),
        ('ta', 'தமிழ்'),
        ('kn', 'ಕನ್ನಡ'),
    ]


def T(key: str, lang: Optional[str] = None) -> str:
    if not lang or lang == 'en' or lang not in SUPPORTED:
        return DICT.get(key, {}).get('en') or key.replace('_', ' ').title()
    entry = DICT.get(key)
    if not entry:
        return key.replace('_', ' ').title()
    return entry.get(lang) or entry.get('en') or key


def detect(request) -> str:
    """Pick the user's preferred language."""
    c = request.cookies.get('lang')
    if c in SUPPORTED:
        return c
    al = request.headers.get('Accept-Language', '')
    for tok in al.split(','):
        code = tok.strip().split('-')[0].split(';')[0]
        if code in SUPPORTED:
            return code
    return 'en'
