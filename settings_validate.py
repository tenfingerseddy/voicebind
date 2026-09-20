"""Validate GUI edits using the real routing stack, without dispatch or API calls."""
import json
import sys
from configuration import validate_config
from bookmarks import Store, explicit_request
from desktop_core.apps import AppCatalog
from dictation_target import parse_start
from personalization import phrase_key
from router import Router


def validate(cfg):
    validate_config(cfg)
    aliases=dict(cfg.get('apps',{}))
    if 'files' in aliases: aliases.setdefault('file manager',aliases['files'])
    catalog=AppCatalog(alias_overrides=aliases)
    for name in aliases: catalog.resolve(name)
    router=Router(catalog,cfg)
    bookmarks=Store()
    for name,command in cfg.get('phrases',{}).items():
        if parse_start(command,catalog) is not None: continue
        request=explicit_request(command)
        if request:
            if request[0] in {'restore','delete'}: bookmarks.resolve(request[1])
            continue
        try:
            bookmark_name=phrase_key(command)
            for prefix in ('restore bookmark ', 'load bookmark ', 'bookmark '):
                if bookmark_name.startswith(prefix): bookmark_name=bookmark_name[len(prefix):]; break
            bookmarks.resolve(bookmark_name); continue
        except ValueError: pass
        try: proposal=router.local(command)
        except ValueError as error: raise ValueError(f'“{name}”: choose a supported command, such as “open files”. {error}') from error
        if proposal.verdict not in {'act','confirm'}: raise ValueError(f'“{name}”: this command needs more detail')
    return cfg


if __name__=='__main__':
    try: validate(json.load(sys.stdin))
    except Exception as error: sys.exit(str(error))
