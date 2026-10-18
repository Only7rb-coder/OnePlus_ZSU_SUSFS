#!/usr/bin/env python3
"""Filter only incompatible manual KernelSU root/init hook hunks from SUSFS 50 patch."""
import argparse,re
from pathlib import Path
DROP=re.compile(r"ksu_handle_(?:execveat|faccessat|stat|vfs_fstat|sys_read)|ksu_su_compat_enabled|ksu_is_init_rc_hook_enabled|__ksu_is_allow_uid_for_current|ksu_is_input_hook_enabled|ksu_handle_input_handle_event|ksu_handle_setresuid|ksu_install_su_fd")
def transform(text):
    lines=text.splitlines(True); out=[]; i=0; dropped=0
    while i<len(lines):
        if not lines[i].startswith('@@ '): out.append(lines[i]); i+=1; continue
        header=lines[i]; i+=1; body=[]
        while i<len(lines) and not lines[i].startswith('@@ ') and not lines[i].startswith('diff --git '): body.append(lines[i]); i+=1
        additions=''.join(x[1:] for x in body if x.startswith('+') and not x.startswith('+++'))
        if DROP.search(additions): dropped+=1
        else: out += [header]+body
    if not dropped: raise SystemExit('no incompatible manual-hook hunks matched')
    result=''.join(out)
    if any(DROP.search(x[1:]) for x in result.splitlines() if x.startswith('+') and not x.startswith('+++')):
        raise SystemExit('forbidden manual-hook additions remain')
    return result
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input'); ap.add_argument('output'); a=ap.parse_args(); Path(a.output).write_text(transform(Path(a.input).read_text()))
if __name__=='__main__': main()
