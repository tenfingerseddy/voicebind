"""Explicit CLI operations; voice replacements/deletions use spoken confirmation."""
import argparse
import json
from bookmarks import Bookmarks, Store
from configuration import load_config
from desktop_core.apps import AppCatalog
from desktop_core.desktop import DesktopController
from language import add_role_aliases


def manager():
    cfg = load_config(); aliases = dict(cfg.get('apps', {}))
    if 'files' in aliases: aliases.setdefault('file manager', aliases['files'])
    catalog = AppCatalog(alias_overrides=aliases); add_role_aliases(catalog)
    return Bookmarks(DesktopController(catalog, cfg)), cfg


def main():
    parser = argparse.ArgumentParser(description='Save and restore named desktop bookmarks')
    parser.add_argument('action', choices=['list','save','restore','delete','phrases'])
    parser.add_argument('name', nargs='?')
    parser.add_argument('phrases', nargs='*')
    parser.add_argument('--replace', action='store_true')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'list':
            print(json.dumps(Store().read(), indent=2)); return
        if not args.name: parser.error('Give the bookmark name in quotes')
        bookmarks,cfg = manager()
        if args.action == 'phrases':
            bookmarks.store.aliases(args.name,args.phrases,cfg)
            print('Bookmark phrases saved. They are available immediately.'); return
        plan = bookmarks.prepare(f'{args.action} bookmark {args.name}', cfg)
        if plan.confirm and not (args.replace if args.action=='save' else args.yes):
            parser.error('Use --replace to update an existing bookmark, or --yes to delete one')
        result = bookmarks.execute(plan)
        print(result['message'])
        if not result['ok']: raise SystemExit(1)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, str(error)+'\n')


if __name__=='__main__': main()
