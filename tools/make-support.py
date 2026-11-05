"""Small development-only Make help/cleanup helper; never traverses environments."""
from pathlib import Path
import re,shutil,subprocess,sys
root=Path(__file__).resolve().parents[1]
owned=['build', 'dist', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', '.coverage', 'src/ocrizer.egg-info']
action=sys.argv[1] if len(sys.argv)>1 else 'help'
if action=='help':
    print('GNU Make development commands (the default only prints this help):')
    seen=set()
    for line in (root/'Makefile').read_text(encoding='utf-8').splitlines():
        match=re.match(r'^([A-Za-z0-9][\w.-]*(?:\s+[A-Za-z0-9][\w.-]*)*):[^#]*##\s*(.+)$',line)
        if match and match[1] not in seen:print('  '+match[1].ljust(25)+match[2]);seen.add(match[1])
    print('See MAKE.md for executable overrides, prerequisites and cleanup scope.')
elif action=='clean':
    selected=sys.argv[2:] or owned
    targets=[]
    for value in selected:
        candidate=root/value
        target=candidate.resolve()
        if not value or target==root or root not in target.parents:raise SystemExit('Refusing output outside the project: '+value)
        rel=target.relative_to(root).as_posix()
        if not any(rel==item or rel.startswith(item+'/') for item in owned):raise SystemExit('Not an owned generated output: '+value)
        current=candidate
        while current!=root:
            if current.is_symlink() or getattr(current,'is_junction',lambda:False)():raise SystemExit('Refusing symlink/junction output: '+value)
            current=current.parent
        tracked=subprocess.run(['git','ls-files','--',rel],cwd=root,text=True,capture_output=True)
        if tracked.returncode!=0:raise SystemExit('Git must succeed to verify source safety')
        if tracked.stdout.strip():raise SystemExit('Refusing tracked source: '+rel)
        targets.append(target)
    for target in targets:
        if target.is_dir():shutil.rmtree(target)
        elif target.exists():target.unlink()
else:raise SystemExit('Unknown Make helper operation: '+action)
