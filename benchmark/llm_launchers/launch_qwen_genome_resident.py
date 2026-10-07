"""External scheduler adapter: frozen benchmark, resident serialized Ollama."""
from __future__ import annotations
import contextlib
import hashlib
import json
import msvcrt
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path("SET_THIS_PATH")
METHODS = REPO / 'benchmark_methods'
sys.path.insert(0, str(METHODS))
sys.path.insert(1, str(REPO))


def stamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def install_resident_queue(agent):
    original_urlopen = agent.urlopen
    lock_path = Path(os.environ['BENCHMARK_OLLAMA_LOCK_PATH'])
    events_path = lock_path.with_name('generation_events.jsonl')

    @contextlib.contextmanager
    def queued_urlopen(request, timeout=None, **kwargs):
        if not (isinstance(request, agent.Request) and request.full_url.endswith('/api/chat')):
            with original_urlopen(request, timeout=timeout, **kwargs) as response:
                yield response
            return
        payload = json.loads(request.data.decode('utf-8'))
        payload['keep_alive'] = -1
        request.data = json.dumps(payload).encode('utf-8')
        seed = (payload.get('options') or {}).get('seed')
        wait_started = time.monotonic()
        print(f'{stamp()} generation queued seed={seed} pid={os.getpid()}', flush=True)
        with lock_path.open('a+b') as lock_file:
            lock_file.seek(0, 2)
            if lock_file.tell() == 0:
                lock_file.write(b'0')
                lock_file.flush()
            while True:
                try:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() - wait_started > 3600:
                        raise TimeoutError('Benchmark generation queue exceeded 3600 seconds')
                    time.sleep(0.2)
            def event(phase, **extra):
                with events_path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(time=stamp(), phase=phase, pid=os.getpid(), seed=seed, **extra)) + '\n')
            try:
                event('start', queue_seconds=round(time.monotonic()-wait_started, 3), keep_alive=-1)
                print(f'{stamp()} generation started seed={seed}', flush=True)
                with original_urlopen(request, timeout=timeout, **kwargs) as response:
                    yield response
                event('finish')
                print(f'{stamp()} generation finished seed={seed}', flush=True)
            except Exception as exc:
                event('error', error=f'{type(exc).__name__}: {exc}')
                raise
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    agent.urlopen = queued_urlopen


def matrix_adapter():
    import run_step2_benchmark_matrix as matrix
    original_jobs = matrix.jobs
    def adapted_jobs(args):
        result = original_jobs(args)
        for job in result:
            if job['group'] != 'llm_genome':
                raise ValueError('This launcher is restricted to llm_genome')
            job['command'][1:2] = [str(Path(__file__).resolve()), '--worker']
        return result
    matrix.jobs = adapted_jobs
    return matrix


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        import local_agent_ablation as agent
        install_resident_queue(agent)
        sys.argv = [str(METHODS / 'local_agent_ablation.py'), *sys.argv[2:]]
        return agent.main()
    matrix = matrix_adapter()
    if '--preflight' in sys.argv:
        from types import SimpleNamespace
        args = SimpleNamespace(run_root=Path("SET_THIS_PATH"),
            step1_root=Path("SET_THIS_PATH"),
            project_context=Path("SET_THIS_PATH"),
            methods=['llm_genome'], llm_modes=['full_feedback'], first_repeat=1,
            repeats=5, budget=50, model='qwen3.8:latest', simulation_timeout=5400)
        tasks = matrix.jobs(args)
        assert len(tasks) == 5
        assert len({str(job['done']) for job in tasks}) == 5
        assert args.step1_root.is_dir() and args.project_context.is_file()
        for job in tasks:
            assert job['command'][2] == '--worker'
        print(json.dumps({'preflight': 'ok', 'jobs': [job['name'] for job in tasks], 'commands': [job['command'] for job in tasks]}, indent=2))
        return 0
    run_root = Path(sys.argv[sys.argv.index('--run-root') + 1]).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    os.environ['BENCHMARK_OLLAMA_LOCK_PATH'] = str(run_root / 'ollama_generation.lock')
    sources = [Path(__file__).resolve(), METHODS / 'run_step2_benchmark_matrix.py',
        METHODS / 'local_agent_ablation.py', METHODS / 'run_one_llm_step2_case.py', METHODS / 'envelope_search_space.py']
    audit = dict(started=stamp(), launcher_pid=os.getpid(), python=sys.executable,
        argv=sys.argv, frozen_repository=str(REPO), generation_keep_alive=-1,
        generation_parallelism=1, simulation_run_parallelism=3,
        lock_path=os.environ['BENCHMARK_OLLAMA_LOCK_PATH'],
        source_sha256={str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources})
    (run_root / 'resident_launcher_manifest.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    return matrix.main()

if __name__ == '__main__':
    raise SystemExit(main())
