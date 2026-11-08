"""Script to relabel existing trajectories with feature schema v4 and train on GPU."""
import time
from pathlib import Path
from engine.contracts import load_json
from engine.learning import FEATURE_SCHEMA_V4, signed
from engine.learn_data import read_dataset, label_state
from engine.learn_train import train, probe_gpu
from engine.storage import write_json_atomic

def main():
    dataset_path = "runs/m4-remote/7ae57456-cb96-4aaf-8504-ab76de87e226/app/dataset/dataset.json"
    src_coll_path = "runs/m4-remote/7ae57456-cb96-4aaf-8504-ab76de87e226/app/runs/samples/collection.json"
    v4_samples_dir = Path("runs/samples-v4")
    v4_samples_dir.mkdir(parents=True, exist_ok=True)
    
    print("Loading dataset scenarios...")
    data, scenarios = read_dataset(dataset_path)
    src_coll = load_json(src_coll_path)
    
    entries = []
    print(f"Relabeling {len(src_coll['entries'])} trajectories to Feature Schema v4 (48 features)...")
    t0 = time.time()
    
    for idx, e in enumerate(src_coll['entries']):
        src_file = Path(src_coll_path).parent / e['path']
        sample = load_json(src_file)
        s = scenarios[sample['scenario_id']]
        
        # Relabel each group's state moves using version=4
        new_groups = []
        for g in sample['groups']:
            relabeled_g = label_state(s, g['solution'], version=4)
            new_groups.append(relabeled_g)
            
        sample['feature_schema'] = FEATURE_SCHEMA_V4
        sample['groups'] = new_groups
        sample = signed(sample, 'samples_hash')
        
        dest_file = v4_samples_dir / e['path']
        write_json_atomic(dest_file, sample)
        entries.append(dict(path=e['path'], samples_hash=sample['samples_hash'], 
                            status=sample['status'], groups=len(sample['groups'])))
        if (idx + 1) % 20 == 0 or idx + 1 == len(src_coll['entries']):
            print(f"  Processed {idx + 1}/{len(src_coll['entries'])} files in {time.time()-t0:.1f}s")
            
    v4_coll = signed(dict(
        schema_version=1,
        kind='sample_collection',
        dataset_hash=data['dataset_hash'],
        entries=sorted(entries, key=lambda x: x['path']),
        expected=len(entries),
        scenario_splits=src_coll['scenario_splits'],
        status='completed'
    ), 'collection_hash')
    
    coll_file = v4_samples_dir / 'collection.json'
    write_json_atomic(coll_file, v4_coll)
    print(f"Saved v4 collection to {coll_file} (collection_hash: {v4_coll['collection_hash']})")
    
    # Verify GPU probe
    print("Probing GPU with v4 architecture [48, 64, 32, 2]...")
    probe = probe_gpu(version=4)
    print("GPU probe result:", probe)
    
    # Train
    print("Starting 50-epoch training on GPU...")
    res = train(coll_file, "runs/m4-v4-gpu", epochs=50, version=4, try_gpu=True)
    print("Training finished successfully!")
    print(f"Checkpoint: {res['checkpoint']}")
    print(f"Model hash: {res['model_hash']}")
    print(f"Selected rank: {res['selected_rank']}")
    print(f"Duration: {res['duration_s']:.2f}s on {res['training_device']}")

if __name__ == '__main__':
    main()
