import json


WMCDA_CRITERIA = [
    {
        'key': 'cost',
        'label': 'Cost',
        'config_key': 'wmcda_w_cost',
        'default': 35,
    },
    {
        'key': 'time',
        'label': 'Time',
        'config_key': 'wmcda_w_time',
        'default': 30,
    },
    {
        'key': 'weight',
        'label': 'Weight / Volume',
        'config_key': 'wmcda_w_weight',
        'default': 20,
    },
    {
        'key': 'distance',
        'label': 'Distance',
        'config_key': 'wmcda_w_distance',
        'default': 15,
    },
]

WMCDA_WEIGHT_KEYS = [item['config_key'] for item in WMCDA_CRITERIA]
WMCDA_METHOD_KEYS = [
    'wmcda_weight_method',
    'wmcda_ahp_matrix',
    'wmcda_ahp_consistency_ratio',
]

AHP_RANDOM_INDEX = {
    1: 0.00,
    2: 0.00,
    3: 0.58,
    4: 0.90,
    5: 1.12,
    6: 1.24,
    7: 1.32,
    8: 1.41,
    9: 1.45,
    10: 1.49,
}


def _coerce_weight(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def load_wmcda_weights(getter):
    """Return configured WMCDA weights as percentage and decimal mappings."""
    weights_pct = {}
    for item in WMCDA_CRITERIA:
        raw = getter(item['config_key'], str(item['default']))
        weights_pct[item['key']] = _coerce_weight(raw, item['default'])

    total = sum(weights_pct.values())
    if total <= 0:
        weights_pct = {item['key']: float(item['default']) for item in WMCDA_CRITERIA}
        total = sum(weights_pct.values())

    weights_decimal = {
        key: value / total
        for key, value in weights_pct.items()
    }
    return weights_pct, weights_decimal


def wmcda_weight_rows(getter):
    weights_pct, _ = load_wmcda_weights(getter)
    return [
        {
            'key': item['key'],
            'label': item['label'],
            'config_key': item['config_key'],
            'value': weights_pct[item['key']],
        }
        for item in WMCDA_CRITERIA
    ]


def normalize_weight_percentages(weights_decimal):
    raw = {
        item['key']: round(weights_decimal[item['key']] * 100)
        for item in WMCDA_CRITERIA
    }
    diff = 100 - sum(raw.values())
    if diff:
        top_key = max(weights_decimal, key=weights_decimal.get)
        raw[top_key] += diff
    return raw


def pairwise_pairs():
    criteria = WMCDA_CRITERIA
    pairs = []
    for i, left in enumerate(criteria):
        for right in criteria[i + 1:]:
            pairs.append({
                'field': f"ahp_{left['key']}_{right['key']}",
                'left': left,
                'right': right,
            })
    return pairs


def build_ahp_matrix(pair_values):
    size = len(WMCDA_CRITERIA)
    matrix = [[1.0 for _ in range(size)] for _ in range(size)]
    key_to_index = {item['key']: i for i, item in enumerate(WMCDA_CRITERIA)}

    for pair in pairwise_pairs():
        left_key = pair['left']['key']
        right_key = pair['right']['key']
        raw = pair_values.get(pair['field'], '1')
        try:
            comparison = float(raw)
        except (TypeError, ValueError):
            comparison = 1.0
        if comparison == 0:
            comparison = 1.0
        magnitude = min(9.0, max(1.0, abs(comparison)))
        value = magnitude if comparison > 0 else 1.0 / magnitude
        left_i = key_to_index[left_key]
        right_i = key_to_index[right_key]
        matrix[left_i][right_i] = value
        matrix[right_i][left_i] = 1.0 / value
    return matrix


def calculate_ahp_weights(pair_values):
    matrix = build_ahp_matrix(pair_values)
    size = len(matrix)
    column_sums = [
        sum(matrix[row][col] for row in range(size))
        for col in range(size)
    ]
    normalized = [
        [
            matrix[row][col] / column_sums[col]
            for col in range(size)
        ]
        for row in range(size)
    ]
    weights_decimal = {
        WMCDA_CRITERIA[row]['key']: sum(normalized[row]) / size
        for row in range(size)
    }

    weight_vector = [weights_decimal[item['key']] for item in WMCDA_CRITERIA]
    weighted_sum = [
        sum(matrix[row][col] * weight_vector[col] for col in range(size))
        for row in range(size)
    ]
    lambda_max = sum(
        weighted_sum[i] / weight_vector[i]
        for i in range(size)
        if weight_vector[i]
    ) / size
    consistency_index = (lambda_max - size) / (size - 1) if size > 1 else 0
    random_index = AHP_RANDOM_INDEX.get(size, 0)
    consistency_ratio = consistency_index / random_index if random_index else 0

    weights_pct = normalize_weight_percentages(weights_decimal)
    return {
        'matrix': matrix,
        'weights_decimal': weights_decimal,
        'weights_pct': weights_pct,
        'lambda_max': lambda_max,
        'consistency_index': consistency_index,
        'consistency_ratio': consistency_ratio,
    }


def serialize_ahp_matrix(matrix):
    return json.dumps([[round(value, 6) for value in row] for row in matrix])


def parse_ahp_matrix(raw):
    try:
        matrix = json.loads(raw or '')
    except (TypeError, ValueError):
        return None
    if not isinstance(matrix, list) or len(matrix) != len(WMCDA_CRITERIA):
        return None
    return matrix
