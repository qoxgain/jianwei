"""Fixed A.1 baseline: blank tool output and stale child lease, generated files only."""
import argparse,asyncio,json,subprocess
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()=='5f150e7daf982c3cb0c9066691f25e7b34961bc8'
    a.output.mkdir(parents=True,exist_ok=False)
    from packages.ingestion import recipe
    result={}
    for name in ['ffmpeg','ffprobe']:
        tool=a.output/name;tool.write_text('#!/bin/sh\nprintf "   \\n \\t\\n"\n');tool.chmod(0o700)
        record=recipe._tool(str(tool.resolve()),tool.stat().st_size,tool.stat().st_mtime_ns)
        result[name]={'accepted_whitespace_version':not record['version'].strip(),'version_repr':repr(record['version'])}
    from infra.file_gate import file_gate
    async def lease():
        gate=file_gate(a.output/'generated.lock',wait_timeout=.05);ready=asyncio.Event()
        async def child():
            await ready.wait();held=gate.held()
            async with gate.async_hold():(a.output/'unauthorized-generated-marker').write_text('child entered')
            return held
        async with gate.async_hold():task=asyncio.create_task(child())
        competitor=gate.try_acquire_raw();assert competitor is not None
        try:
            ready.set();held=await task
            return {'stale_child_held':held,'child_entered_while_competitor_owns_file_lock':(a.output/'unauthorized-generated-marker').exists()}
        finally:gate.release_raw(competitor)
    result['lease']=asyncio.run(lease());(a.output/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
