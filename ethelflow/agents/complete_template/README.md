# complete_template

Render a **Mustache** template (via `chevron`) using a provided `fields` JSON object, with **conditional rendering** via Mustache sections.

This service is intentionally small and “pure”: it **does not** talk to Postgres, S3, or the model catalog.

---

## What it does

- Takes:
  - a Mustache template string, and
  - a JSON object of fields (free-form keys)
- Returns:
  - the rendered string
- Supports “render this block only if a variable is non-empty” using Mustache sections (`{{#var}}...{{/var}}`)
- Optionally **normalizes empty strings / empty lists / empty dicts to `False`** so sections behave as you expect.

---

## API

### `POST /complete_template`

**Request** (`CompleteTemplateRequest`)

```json
{
  "template": "You are an assistant.\n\n{{#prompt}}The user asks:\n{{prompt}}\n{{/prompt}}\n\n{{#weather}}The weather is:\n{{weather}}\n{{/weather}}\n",
  "fields": {
    "prompt": "What is the meaning of life?",
    "weather": "Sunny day"
  },
  "normalize_empties": true
}
```

**Response** (`CompleteTemplateResponse`)

```json
{
  "success": true,
  "message": "",
  "rendered": "You are an assistant.\n\nThe user asks:\nWhat is the meaning of life?\n\nThe weather is:\nSunny day\n"
}
```

**Notes on conditional blocks**

- Render a block only if a variable is truthy:

  ```mustache
  {{#weather}}
  The weather is:
  {{weather}}
  {{/weather}}
  ```

- Require *multiple* variables to be present (nested sections):

  ```mustache
  {{#prompt}}
    {{#weather}}
      Prompt: {{prompt}}
      Weather: {{weather}}
    {{/weather}}
  {{/prompt}}
  ```

**About escaping**

- `chevron` follows Mustache behavior: `{{var}}` is HTML-escaped.
- Use `{{{var}}}` if you want raw insertion.
- (You said you don’t want to worry about escaping; for now you can just use `{{{...}}}` where needed.)

---

## Node adapter (LangGraph integration)

**File:** `ethelflow/agents/complete_template/node_adapter.py`  
**Service URL:** `http://complete-template.default.svc:8000/complete_template`

### `complete_template_node(...)`

Default state keys:

- **Inputs**
  - `template` (str)
  - `fields` (dict)
  - `normalize_empties` (bool, optional; default `True`)
- **Outputs**
  - `complete_template_response` (dict; full response)
  - `rendered_template` (str; convenience for downstream)

Example use in a flow:

```python
from ethelflow.agents.complete_template.node_adapter import complete_template_node

render = complete_template_node(
    template_key="template",
    fields_key="fields",
    normalize_empties_key="normalize_empties",
    output_key="complete_template_response",
    output_rendered_key="rendered_template",
)
workflow.add_node("complete_template", render)
```

---

## Kubernetes

Typical deployment/service naming:

- **Deployment:** `complete-template`
- **Service:** `complete-template`
- **Port:** 8000
- **Command:** `python -m ethelflow.agents.complete_template`
- **Image:** currently `ethelflow:latest` (your current local-dev convention)

---

## Troubleshooting

- **I expected a block to disappear but it still rendered**
  - Ensure you used a **section** (`{{#var}}...{{/var}}`) rather than just `{{var}}`.
  - With `normalize_empties=true`, empty strings like `""` (or `"   "`) become `False`, and empty lists/dicts become `False`.

- **I see HTML escaping in the output**
  - Use triple braces: `{{{var}}}`.

- **Service returns HTTP 500**
  - Check the payload types (template must be a non-empty string; fields must be a dict).
  - Inspect the pod logs for the exception from `complete_template failed`.
