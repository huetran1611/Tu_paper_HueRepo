"""Dataset IDs retained from the original Hanoi experiment."""

DATASETS = (
    'set_03', 'set_05', 'set_07', 'set_08', 'set_09',
    'set_11', 'set_12', 'set_14', 'set_15', 'set_16',
    'set_17', 'set_19', 'set_20', 'set_21', 'set_23',
    'set_25', 'set_27', 'set_30', 'set_32', 'set_33',
)
INSTANCE_INDICES = tuple(int(dataset.split('_')[1]) for dataset in DATASETS)
DATA_ROOT = 'instance_hanoi/datasets/hanoi_traffic/Hanoi_selected_20'
