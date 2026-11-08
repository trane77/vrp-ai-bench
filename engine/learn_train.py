"""Supervised two-head MLP training; selection sees validation groups only."""
import copy
import time
from pathlib import Path

from .contracts import InputError, load_json
from .learning import FEATURE_SCHEMA, FEATURE_SCHEMA_V2, FEATURE_SCHEMA_V3, FEATURE_SCHEMA_V4, Policy, signed, verify_hash
from .storage import write_json_atomic


def export_model(network, metadata, version=1):
    layers = [dict(weight=layer.weight.detach().cpu().tolist(), bias=layer.bias.detach().cpu().tolist())
              for layer in network if hasattr(layer,'weight')]
    if version == 1:
        arch = [30,32,16,2]
        schema = FEATURE_SCHEMA
    elif version == 2:
        arch = [38,64,32,2]
        schema = FEATURE_SCHEMA_V2
    elif version == 3:
        arch = [46,64,32,2]
        schema = FEATURE_SCHEMA_V3
    elif version == 4:
        arch = [48,64,32,2]
        schema = FEATURE_SCHEMA_V4
    else:
        raise InputError(f'unsupported model version: {version}')
    return signed(dict(schema_version=version,kind='learned_policy',architecture=arch,
                       feature_schema=schema,layers=layers,metadata=metadata), 'model_hash')


def make_network(version=1):
    import torch
    if version == 4:
        return torch.nn.Sequential(torch.nn.Linear(48,64),torch.nn.ReLU(),torch.nn.Linear(64,32),
                                   torch.nn.ReLU(),torch.nn.Linear(32,2))
    if version == 3:
        return torch.nn.Sequential(torch.nn.Linear(46,64),torch.nn.ReLU(),torch.nn.Linear(64,32),
                                   torch.nn.ReLU(),torch.nn.Linear(32,2))
    if version == 2:
        return torch.nn.Sequential(torch.nn.Linear(38,64),torch.nn.ReLU(),torch.nn.Linear(64,32),
                                   torch.nn.ReLU(),torch.nn.Linear(32,2))
    return torch.nn.Sequential(torch.nn.Linear(30,32),torch.nn.ReLU(),torch.nn.Linear(32,16),
                               torch.nn.ReLU(),torch.nn.Linear(16,2))


def probe_gpu(version=1):
    import torch
    started = time.monotonic()
    value = dict(attempted=True, device='cuda', torch=torch.__version__, hip=getattr(torch.version, 'hip', None))
    try:
        if not torch.cuda.is_available(): raise RuntimeError('PyTorch reports no available GPU')
        net = make_network(version=version).to('cuda'); before = net[0].weight.detach().clone()
        optimizer = torch.optim.Adam(net.parameters(),lr=.001)
        in_dim = 48 if version == 4 else (46 if version == 3 else (38 if version == 2 else 30))
        net(torch.ones(8,in_dim,device='cuda')).square().mean().backward(); optimizer.step(); torch.cuda.synchronize()
        if torch.equal(before,net[0].weight): raise RuntimeError('GPU probe did not update weights')
        value.update(status='passed',name=torch.cuda.get_device_name(0),forward_backward_update=True)
    except Exception as exc:
        value.update(status='failed',reason=f'{type(exc).__name__}: {exc}')
    value['duration_s'] = time.monotonic()-started
    return value


def selection_rank(policy, groups):
    ranks = []
    for group in groups:
        rows = group['rows']; scores = policy.predict([r['features'] for r in rows])
        order = sorted(range(len(rows)),key=lambda i:-float(scores[i]))
        rank = next((position/max(1,len(rows)-1) for position,i in enumerate(order) if rows[i]['improvement']),1.)
        ranks.append(rank)
    return sum(ranks)/len(ranks)


def train(collection_path, directory, deadline=None, cancelled=lambda:False, try_gpu=True, epochs=50, version=None):
    import numpy as np
    import torch
    started = time.monotonic(); root = Path(directory); root.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    collection_path = Path(collection_path); collection = load_json(collection_path)
    verify_hash(collection,'collection_hash')
    if collection['status'] != 'completed': raise InputError('incomplete sample collection; training stopped')
    groups = {'training':[], 'validation':[]}; identities = {}
    schema_map = {1: FEATURE_SCHEMA, 2: FEATURE_SCHEMA_V2, 3: FEATURE_SCHEMA_V3, 4: FEATURE_SCHEMA_V4}
    detected_ver = version
    for entry in collection['entries']:
        samples = load_json(collection_path.parent/entry['path']); verify_hash(samples,'samples_hash')
        s_schema = samples.get('feature_schema', {})
        s_ver = s_schema.get('version')
        if detected_ver is None:
            detected_ver = s_ver
        expected_schema = schema_map.get(detected_ver)
        if samples['samples_hash'] != entry['samples_hash'] or samples['feature_schema'] != expected_schema or samples['status'] != 'completed':
            raise InputError('incompatible samples')
        split = samples['split']
        if split not in groups: raise InputError('held-out samples are forbidden in training')
        identity = samples['scenario_hash']
        if collection['scenario_splits'].get(identity) != split: raise InputError('sample split is not the frozen scenario split')
        if identity in identities and identities[identity] != split: raise InputError('scenario leakage between splits')
        identities[identity] = split; groups[split] += samples['groups']
    if detected_ver not in (1, 2, 3, 4):
        raise InputError(f'unsupported sample schema version: {detected_ver}')
    ver = detected_ver
    rows = [r for group in groups['training'] for r in group['rows']]
    if not rows or not groups['validation'] or not any(r['improvement'] for r in rows) or not any(r['improvement'] for g in groups['validation'] for r in g['rows']):
        raise InputError('training/validation have insufficient observed improving moves')
    attempts = [probe_gpu(version=ver)] if try_gpu else []
    device = 'cuda' if attempts and attempts[0]['status']=='passed' else 'cpu'
    metadata = dict(dataset_hash=collection['dataset_hash'],collection_hash=collection['collection_hash'],
                    schema_version=ver,
                    training_device=device,gpu_attempts=attempts,torch=torch.__version__,numpy=np.__version__,
                    training_seeds=[0,1,2],learning_rate=.001,batch_size=1024,max_epochs=epochs,
                    selection='mean normalized rank of first actual improvement; ties epoch then seed',
                    validation_groups=len(groups['validation']),training_groups=len(groups['training']))
    write_json_atomic(root/'training-start.json',metadata)
    x = torch.tensor([r['features'] for r in rows],dtype=torch.float32,device=device)
    y = torch.tensor([[float(r['improvement']),r['gain']] for r in rows],dtype=torch.float32,device=device)
    positives = float(y[:,0].sum()); negatives = len(rows)-positives
    if not positives or not negatives: raise InputError('training needs both label classes')
    positive_weight = torch.tensor(negatives/positives,device=device)
    best = None; history = []; completed_seeds = []; parity_error = 0.
    for seed in (0,1,2):
        torch.manual_seed(seed); network=make_network(version=ver).to(device)
        initial = network[0].weight.detach().clone()
        optimizer=torch.optim.Adam(network.parameters(),lr=.001)
        for epoch in range(1, epochs + 1):
            if cancelled() or (deadline is not None and time.monotonic() >= deadline):
                write_json_atomic(root/'training-incomplete.json',dict(status='incomplete',history=history,duration_s=time.monotonic()-started,metadata=metadata))
                raise RuntimeError('training deadline/cancellation; checkpoint selection not frozen')
            permutation=torch.randperm(len(x),device=device)
            for batch in permutation.split(1024):
                if cancelled() or (deadline is not None and time.monotonic()>=deadline):
                    write_json_atomic(root/'training-incomplete.json',dict(status='incomplete',history=history,metadata=metadata,duration_s=time.monotonic()-started))
                    raise RuntimeError('training deadline/cancellation during batch')
                optimizer.zero_grad(); prediction=network(x[batch]); target=y[batch]
                loss=torch.nn.functional.binary_cross_entropy_with_logits(prediction[:,0],target[:,0],pos_weight=positive_weight)
                loss=loss+torch.nn.functional.smooth_l1_loss(torch.nn.functional.softplus(prediction[:,1]),target[:,1])
                loss.backward(); optimizer.step()
            if device=='cuda': torch.cuda.synchronize()
            if torch.equal(initial,network[0].weight): raise RuntimeError('training did not update weights')
            model=export_model(network,dict(metadata,seed=seed,epoch=epoch),version=ver)
            policy=Policy(model); rank=selection_rank(policy,groups['validation'])
            with torch.no_grad():
                raw=network(x[:128]).cpu()
                expected=torch.sigmoid(raw[:,0])*torch.nn.functional.softplus(raw[:,1])
            actual=policy.predict(x[:128].cpu().numpy())
            error=float(np.max(np.abs(actual-expected.numpy())))
            if not np.allclose(actual,expected.numpy(),rtol=2e-5,atol=2e-6): raise RuntimeError('export/runtime numeric parity failure')
            parity_error=max(parity_error,error)
            key=(rank,epoch,seed); history.append(dict(seed=seed,epoch=epoch,rank=rank,loss=float(loss.detach().cpu())))
            if best is None or key<best[0]:
                best=(key,copy.deepcopy(model))
                write_json_atomic(root/'best-observed.json',model,overwrite=True)
        completed_seeds.append(seed)
    metadata.update(duration_s=time.monotonic()-started,completed_seeds=completed_seeds,
                    parity_max_abs_error=parity_error,selected_rank=best[0][0],history=history,status='completed')
    selected=best[1]; selected['metadata'].update(training_duration_s=metadata['duration_s'],selected_rank=best[0][0])
    selected=signed({k:v for k,v in selected.items() if k!='model_hash'},'model_hash')
    write_json_atomic(root/'checkpoint.json',selected)
    write_json_atomic(root/'training.json',metadata)
    return dict(checkpoint=str(root/'checkpoint.json'),model_hash=selected['model_hash'],**metadata)
