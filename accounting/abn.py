"""ABN and ACN validation.

An ABN carries a checksum, so a typo or an invented number can be caught
before it reaches a TPAR or a tax invoice. That matters in construction: a
subcontractor who quotes an ABN that does not check out has effectively not
quoted one, and 47% has to be withheld from their payment.

Passing the checksum only proves the number is well formed. It does not prove
the ABN is active, belongs to that person, or is registered for GST - check
those on ABN Lookup at abr.business.gov.au.
"""
from __future__ import annotations

# Weights applied to each digit of an ABN, after 1 is subtracted from the first.
ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
ACN_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 1)


def normalise(value) -> str:
    """Strip spaces and punctuation from a quoted ABN or ACN."""
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def format_abn(value) -> str:
    """Format as the ATO prints it: 51 824 753 556."""
    digits = normalise(value)
    if len(digits) != 11:
        return str(value or '')
    return f'{digits[:2]} {digits[2:5]} {digits[5:8]} {digits[8:]}'


def format_acn(value) -> str:
    digits = normalise(value)
    if len(digits) != 9:
        return str(value or '')
    return f'{digits[:3]} {digits[3:6]} {digits[6:]}'


def is_valid_abn(value) -> bool:
    digits = normalise(value)
    if len(digits) != 11 or digits[0] == '0':
        return False
    numbers = [int(d) for d in digits]
    numbers[0] -= 1
    total = sum(n * w for n, w in zip(numbers, ABN_WEIGHTS))
    return total % 89 == 0


def is_valid_acn(value) -> bool:
    digits = normalise(value)
    if len(digits) != 9:
        return False
    numbers = [int(d) for d in digits]
    total = sum(n * w for n, w in zip(numbers[:8], ACN_WEIGHTS))
    remainder = total % 10
    check = (10 - remainder) % 10
    return check == numbers[8]


def check_abn(value) -> str:
    """Return an error message, or '' when the ABN is well formed."""
    digits = normalise(value)
    if not digits:
        return 'no ABN supplied'
    if len(digits) != 11:
        return f'an ABN has 11 digits, this one has {len(digits)}'
    if not is_valid_abn(digits):
        return (f'{format_abn(digits)} fails the ABN checksum - it is either a '
                'typo or not a real ABN')
    return ''
