"""Preserve MCP argument validation at the server boundary."""


def prepare_tool_arguments(spec: dict, parameters: dict, tool_type: str) -> dict:
    schema = spec.get('parameters', {})
    allowed = schema.get('properties', {}).keys()
    if tool_type == 'mcp':
        if not isinstance(parameters, dict):
            raise ValueError('MCP tool arguments must be a JSON object.')
        # Explicitly open objects and typed maps may accept extra keys; their
        # server owns value validation. Otherwise never discard an invented filter.
        additional = schema.get('additionalProperties', False)
        accepts_extra = additional is True or isinstance(additional, dict)
        if 'properties' in schema and not accepts_extra and set(parameters) - set(allowed):
            raise ValueError(
                'Unsupported MCP arguments. Use only root fields in the discovered tool schema; '
                'no arguments were discarded.'
            )
        return dict(parameters)
    # Keep the established behaviour of builtin, terminal and direct tools.
    return {key: value for key, value in parameters.items() if key in allowed}
