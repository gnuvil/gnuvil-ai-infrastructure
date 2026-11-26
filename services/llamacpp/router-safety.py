#!/usr/bin/env python3
"""Keep the pinned llama.cpp router from evicting or killing model processes."""
from pathlib import Path

path = Path('/opt/llama.cpp/tools/server/server-models.cpp')
source = path.read_text()
changes = [
    ('    unload_lru();\n\n    std::lock_guard<std::mutex> lk(mutex);',
     '    // The capacity check below refuses a second model instead of evicting one.\n'
     '    std::lock_guard<std::mutex> lk(mutex);'),
    ('                    // timeout, force kill\n'
     '                    SRV_WRN("force-killing model instance name=%s after %d seconds timeout\\n", name.c_str(), stop_timeout);\n'
     '                    subprocess_terminate(child_proc.get());\n'
     '                    return;',
     '                    SRV_WRN("model instance name=%s did not stop within %d seconds; leaving it running\\n", name.c_str(), stop_timeout);\n'
     '                    return;'),
    ('            SRV_WRN("old process for model name=%s is still alive, this is unexpected\\n", name.c_str());\n'
     '            subprocess_terminate(old_instance.subproc.get()); // force kill',
     '            throw std::runtime_error("previous model process is still running");'),
]
for old, new in changes:
    if source.count(old) != 1:
        raise SystemExit('Pinned router source did not match expected safety patch')
    source = source.replace(old, new, 1)
path.write_text(source)
