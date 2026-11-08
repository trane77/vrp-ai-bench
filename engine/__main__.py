"""Source-tree CLI. Exit 0 success, 1 operational error, 2 invalid input,
3 invalid route proposal, 4 completed search without a feasible solution.
"""
import time

_STARTUP = time.monotonic()

import argparse
import json
import sys

from .contracts import InputError, RUN, check, load_json, validate_scenario
from .solver import scenario_hash, solve
from .storage import write_json_atomic
from .validation import validate_solution
from .contracts_v2 import run_schema


def main(argv=None):
    parser = argparse.ArgumentParser(description='Synthetic CPU routing baseline')
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate', help='validate scenario and optional solution/run')
    validate.add_argument('scenario')
    validate.add_argument('--solution')
    run = commands.add_parser('solve', help='construct and improve CPU routes')
    run.add_argument('scenario')
    run.add_argument('--seed', type=int, default=0)
    run.add_argument('--budget-s', type=float, default=1.0)
    run.add_argument('--max-evaluations', type=int)
    run.add_argument('--restarts', type=int, default=8)
    run.add_argument('--output', required=True)
    run.add_argument('--overwrite', action='store_true')
    city = commands.add_parser('city', help='explicit road dataset preparation')
    city.add_argument('action', choices=['prepare','list','verify','serve','stop'])
    city.add_argument('--data-root', default='data')
    city.add_argument('--source')
    city.add_argument('--city', choices=['bologna','modena'], default='bologna')
    worker = commands.add_parser('worker', help='separate durable queue worker')
    worker.add_argument('--root', default='runs')
    worker.add_argument('--data-root', default='data')
    worker.add_argument('--once', action='store_true')
    worker.add_argument('--stop', action='store_true', help='stop after the current job; keep queued jobs')
    serve = commands.add_parser('serve', help='HTTP service; LAN requires TLS and token')
    serve.add_argument('--root', default='runs')
    serve.add_argument('--data-root', default='data')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    serve.add_argument('--certfile')
    serve.add_argument('--keyfile')
    serve.add_argument('--token-file')
    serve.add_argument('--allowed-origin', action='append', default=[])
    serve.add_argument('--server-name', help='DNS name used by the browser and certificate')
    serve.add_argument('--allowed-client-ip', action='append', default=[])
    experiment = commands.add_parser('experiment', help='frozen classical pilot')
    experiment.add_argument('action', choices=['freeze','run','status','report'])
    experiment.add_argument('inputs', nargs='*')
    experiment.add_argument('--output')
    experiment.add_argument('--root', default='runs')
    learn = commands.add_parser('learn', help='M4 supervised pipeline')
    learn.add_argument('action', choices=['dataset','collect','train','evaluate','campaign','audit'])
    learn.add_argument('input', nargs='?')
    learn.add_argument('--output')
    learn.add_argument('--checkpoint')
    learn.add_argument('--root', default='runs/m4')
    learn.add_argument('--data-root', default='data')
    learn.add_argument('--cpu', action='store_true')
    learn.add_argument('--version', type=int, default=None, choices=[1, 2, 3, 4])
    learn.add_argument('--epochs', type=int, default=50)
    learn.add_argument('--timeout-s', type=float, default=None)
    remote = commands.add_parser('remote', help='verified SSH batch transport')
    remote.add_argument('action', choices=['doctor','submit','status','cancel','fetch'])
    remote.add_argument('input', nargs='?')
    remote.add_argument('--host', default='Ai360')
    remote.add_argument('--image')
    remote.add_argument('--output')
    remote.add_argument('--root', default='runs/m4-imports')
    args = parser.parse_args(argv)
    try:
        if args.command == 'learn':
            from .learn_cli import execute
            print(json.dumps(execute(args), allow_nan=False))
            return 0
        if args.command == 'remote':
            from .remote import execute
            print(json.dumps(execute(args), allow_nan=False))
            return 0
        if args.command == 'experiment':
            from .experiments import freeze, report
            from .jobs import Jobs
            jobs = Jobs(args.root)
            if args.action == 'freeze':
                if len(args.inputs)!=2 or not args.output: raise InputError('freeze requires two scenario paths and --output')
                value = freeze([load_json(p) for p in args.inputs])
                write_json_atomic(args.output, value)
                print(json.dumps(dict(manifest=args.output, hash=value['manifest_hash'], cases=len(value['cases']))))
            elif args.action == 'run':
                if len(args.inputs)!=1: raise InputError('run requires a manifest path')
                print(json.dumps(jobs.submit('experiment', dict(manifest=load_json(args.inputs[0])))))
            elif args.action == 'status':
                print(json.dumps(jobs.get(args.inputs[0]) if args.inputs else [j for j in jobs.list() if j['kind']=='experiment']))
            else:
                if len(args.inputs)!=1: raise InputError('report requires a campaign job id')
                print(json.dumps(report(jobs.root/args.inputs[0])))
            return 0
        if args.command == 'city':
            from .city import CityRegistry, prepare, serve as serve_osrm, docker
            registry = CityRegistry(args.data_root)
            actions = {'prepare': lambda: prepare(args.data_root,args.source),
                       'list': registry.list, 'verify': lambda: registry.verify(args.city),
                       'serve': lambda: serve_osrm(args.data_root),
                       'stop': lambda: docker('stop','vrp-ai-osrm',capture=True)}
            print(json.dumps(actions[args.action](),indent=2))
            return 0
        if args.command == 'worker':
            from .worker import Worker
            worker = Worker(args.root,args.data_root)
            if args.stop:
                (worker.jobs.root/'worker.stop').touch()
                return 0
            if args.once:
                worker.acquire()
                try: worker.execute_one()
                finally: worker.close()
            else: worker.run()
            return 0
        if args.command == 'serve':
            from pathlib import Path
            import uvicorn
            from .service import create_app
            lan=args.host not in ('127.0.0.1','localhost','::1')
            if bool(args.certfile)!=bool(args.keyfile) or (lan and not (args.certfile and args.token_file)):
                raise InputError('LAN bind requires certfile, keyfile and token-file; TLS needs both certificate and key')
            from urllib.parse import urlparse
            def valid_origin(origin):
                parsed=urlparse(origin)
                return (parsed.scheme=='https' or (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1'))) and bool(parsed.netloc) and not parsed.path and not parsed.query and not parsed.fragment
            if any(not valid_origin(origin) for origin in args.allowed_origin):
                raise InputError('allowed origins must be HTTPS or loopback HTTP origins without paths')
            token=None
            if args.token_file:
                path=Path(args.token_file).resolve()
                if path.is_relative_to(Path(__file__).resolve().parent.parent):
                    raise InputError('token file must be outside the repository')
                token=path.read_text().strip()
                if len(token)<32: raise InputError('token must contain at least 32 characters')
            uvicorn.run(create_app(args.root,args.data_root,token=token,allowed_origins=args.allowed_origin,
                                   allowed_hosts=[args.host,*([args.server_name] if args.server_name else [])],
                                   allowed_client_ips=args.allowed_client_ip),host=args.host,port=args.port,
                        ssl_certfile=args.certfile,ssl_keyfile=args.keyfile,access_log=not bool(token))
            return 0
        load_started = time.monotonic()
        startup_s = load_started - _STARTUP
        data = load_json(args.scenario)
        if args.command == 'solve':
            result = solve(data, seed=args.seed, budget_s=args.budget_s,
                           max_evaluations=args.max_evaluations, restarts=args.restarts,
                           startup_s=startup_s, preparation_s=time.monotonic() - load_started)
            write_json_atomic(args.output, result, overwrite=args.overwrite)
            print(json.dumps({key: result[key] for key in
                              ('run_id', 'feasibility', 'termination_reason', 'timing')}))
            return 0 if result['feasibility'] == 'feasible' else 4
        scenario = validate_scenario(data)
        if not args.solution:
            print(json.dumps(dict(valid=True, scenario_id=scenario['id'])))
            return 0
        document = load_json(args.solution)
        if isinstance(document, dict) and ('run_id' in document or document.get('kind') == 'experiment_run'):
            check(document, run_schema(document))
            if document.get('schema_version') == 4:
                from pathlib import Path
                from .learn_evaluate import hydrate_run
                document = hydrate_run(Path(args.solution).resolve().parent.parent.parent,document)
            embedded = validate_scenario(document['scenario'])
            if scenario_hash(embedded) != document['scenario_hash'] or scenario_hash(scenario) != document.get('original_scenario_hash',document['scenario_hash']):
                raise InputError('run scenario hash does not match supplied scenario')
            solution = document['solution']
            if solution is None:
                if document['feasibility'] != 'unknown' or document['validation'] is not None:
                    raise InputError('inconsistent run without solution')
                print(json.dumps(dict(valid=True, feasibility='unknown', solution=None)))
                return 0
            report = validate_solution(scenario, solution)
            if document['feasibility'] != 'feasible' or not report['feasible'] or document['validation'] != report:
                raise InputError('saved validation/feasibility does not match independent reconstruction')
        else:
            report = validate_solution(scenario, document)
        print(json.dumps(report, allow_nan=False))
        return 0 if report['feasible'] else 3
    except (InputError, OverflowError, RecursionError) as exc:
        print(f'invalid input: {exc}', file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as exc:
        print(f'execution failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
