def omit_none(**kwargs):
    """Drop None values so unset optional params aren't sent to the API."""
    return {k: v for k, v in kwargs.items() if v is not None}
