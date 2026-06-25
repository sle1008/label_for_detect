"""Human-readable duration formatting."""


def format_duration(seconds: float) -> str:
    """Format seconds using ms, s, or min as appropriate."""
    if seconds < 0:
        seconds = 0.0
    if seconds < 1.0:
        return f'{seconds * 1000:.0f}ms'
    if seconds < 60.0:
        return f'{seconds:.2f}s'
    minutes = int(seconds // 60)
    remainder = seconds % 60
    if remainder < 0.05:
        return f'{minutes}min'
    return f'{minutes}min {remainder:.1f}s'
