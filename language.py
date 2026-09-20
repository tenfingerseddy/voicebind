"""Shared phrasing rules, independent of the installed applications and Jev."""
import re

CURRENT = {'this', 'this window', 'this app', 'this application', 'current app',
           'current application', 'current window', 'the current window',
           'the current app', 'the current application', 'the window'}
PREVIOUS = {'it', 'that', 'that app', 'that window', 'that application', 'the same window'}
START = (r'(?:please |can you |could you |would you )?'
         r'(?:open|launch|start|show|close|closed|quit|shut|move|send|put|chuck|stick|'
         r'switch|go|focus|fullscreen|full screen|maximize|maximise|restore|float|tile|'
         r'make|exit|volume|sound|turn|set|mute|unmute|hide|dismiss|bring|pull|fire|'
         r'take|give|resume|pause|stop|lock|dim|brighten)\b')
BOUNDARY = re.compile(r'(?:\s*[,\.]\s*(?:(?:and then|and|then)\s+)?|'
                      r'\s+(?:and then|then|and)\s+)(?=' + START + ')')


def clauses(text):
    """Only split explicit actions; leave coordinated objects to the core parser."""
    return BOUNDARY.split(text)


def canonical(text):
    text = re.sub(r'^(?:(?:can|could|would) you (?:please )?|please[, ]+)', '', text)
    text = re.sub(r',? please$', '', text)
    text = re.sub(r'^(move|open|close|focus),\s*', r'\1 ', text)
    text = re.sub(r'^(?:bring up|pull up|fire up)\b', 'open', text)
    text = re.sub(r'^show me\b', 'show', text)
    text = re.sub(r'^(?:closed|shut|quit)\b', 'close', text)
    text = re.sub(r'^go (?:full screen|fullscreen)$', 'fullscreen', text)
    text = re.sub(r'^make (.+?) (?:not|no longer) (?:full screen|fullscreen|maximi[sz]ed)$', r'restore \1', text)
    text = re.sub(r'^(?:take|get) (.+?) out of (?:full screen|fullscreen)$', r'restore \1', text)
    text = re.sub(r'\bwork spaces?\b', 'workspace', text)
    text = re.sub(r',\s*(?=workspace\b|works (?:best|based)\b)', ' ', text)
    match = re.fullmatch(r'(?:put|chuck|stick|move|send) (.+?)(?: back| over)? (?:on|onto|to) ((?:workspace|desktop) .+)', text)
    if match:
        text = 'move ' + match[1] + ' to ' + match[2]
    # These references are not app names; keep their distinction through parsing.
    references = '|'.join(re.escape(w) for w in sorted(CURRENT | PREVIOUS, key=len, reverse=True))
    text = re.sub(r'(?<!\w)(?:' + references + r')(?!\w)',
                  lambda m: 'voicepreviouswindow' if m[0] in PREVIOUS else 'this window', text)
    return text


def add_role_aliases(catalog):
    for role, aliases in {
        'files': ('file manager', 'file browser', 'file explorer', 'explorer'),
        'browser': ('web browser', 'internet browser'),
        'terminal': ('command line',),
        'code': ('code editor', 'text editor'),
        'outlook': ('email', 'mail'),
    }.items():
        ids = catalog.aliases.get(role, ())
        if len(ids) == 1:
            for alias in aliases:
                catalog.aliases.setdefault(alias, ids)


def app_name(name, catalog):
    if not name:
        return None
    try:
        catalog.resolve(name)
        return name
    except ValueError:
        # Strip grammatical decorations only when the result is a known app.
        plain = re.sub(r'^(?:the|my) ', '', name)
        plain = re.sub(r' (?:app|application|window)$', '', plain)
        catalog.resolve(plain)
        return plain


def undecorate_apps(text, catalog):
    aliases = [re.escape(a) for a, ids in sorted(catalog.aliases.items(), key=lambda p: -len(p[0])) if len(ids) == 1]
    if not aliases:
        return text
    return re.sub(r'(?<!\w)(?:(?:the|my) )?(' + '|'.join(aliases) + r')(?: (?:app|application|window))?(?!\w)',
                  lambda m: m[1], text)
