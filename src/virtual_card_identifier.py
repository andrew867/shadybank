"""The bank's existing internal virtual-card identifier allocation."""


def append_luhn_check_digit(payload):
    total = 0
    for index, digit in enumerate(reversed(payload)):
        value = int(digit)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return payload + str((10 - total % 10) % 10)


def bank_card_identifier(account_id):
    if not isinstance(account_id, int) or not 0 < account_id <= 999999:
        raise ValueError('Internal virtual-card identifier space exhausted')
    return append_luhn_check_digit(f'899798667{account_id:06d}')
