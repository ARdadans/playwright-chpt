import json


def repair_json(text: str) -> dict:
    """
    Attempt to repair common JSON formatting errors, specifically:
    1. Literal newlines inside strings (replace with \\n)
    2. Unescaped double quotes inside strings
    """
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()

    # First try normal parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # We will use a state machine to escape double quotes and literal newlines
    # that appear inside string values.
    repaired_chars = []
    in_string = False
    escape_next = False

    for i, char in enumerate(text):
        if escape_next:
            repaired_chars.append(char)
            escape_next = False
            continue

        if char == "\\":
            repaired_chars.append(char)
            escape_next = True
            continue

        if char == '"':
            # Check if this quote is a structural quote or an unescaped inner quote.
            # A quote is structural if it's the start/end of a string.
            # Heuristic: if we are in a string, and the next characters (ignoring whitespace)
            # are one of: , } ] :
            # Or if it's the end of the text.
            # Then it's a closing structural quote.
            if in_string:
                # Look ahead to see if it's a closing quote
                is_closing = False
                for j in range(i + 1, len(text)):
                    if text[j] in " \t\n\r":
                        continue
                    if text[j] in ",}]:":
                        is_closing = True
                    break
                else:
                    # End of text
                    is_closing = True

                if is_closing:
                    in_string = False
                    repaired_chars.append('"')
                else:
                    # Unescaped inner quote!
                    repaired_chars.append('\\"')
            else:
                # Opening quote
                in_string = True
                repaired_chars.append('"')
            continue

        if char == "\n":
            if in_string:
                repaired_chars.append("\\n")
            else:
                repaired_chars.append(char)
            continue

        if char == "\r":
            if in_string:
                pass  # skip or append \\r
            else:
                repaired_chars.append(char)
            continue

        if char == "\t":
            if in_string:
                repaired_chars.append("\\t")
            else:
                repaired_chars.append(char)
            continue

        repaired_chars.append(char)

    repaired_text = "".join(repaired_chars)

    # Try parsing the repaired text
    return json.loads(repaired_text)
