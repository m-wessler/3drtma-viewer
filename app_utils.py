from datetime import datetime


def date_to_yyyymmdd(date_str: str) -> str:
    """Normalize date string to YYYYMMDD. Accepts YYYY-MM-DD or YYYYMMDD.

    Raises ValueError on invalid input.
    """
    if not date_str:
        raise ValueError('Empty date string')
    if isinstance(date_str, str) and len(date_str) == 8 and date_str.isdigit():
        return date_str
    # support YYYY-MM-DD
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%Y%m%d')
    except Exception:
        raise ValueError('Invalid date format')


def validate_pressure_level(value):
    """Validate and convert pressure level input to int.

    Accepts int or numeric string. Raises ValueError for missing/invalid values.
    """
    if value is None or (isinstance(value, str) and value.strip() == ''):
        raise ValueError('pressure_level is required')
    try:
        return int(value)
    except Exception:
        raise ValueError('Invalid pressure_level; must be integer')
