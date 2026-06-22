# Test commands

## Reasoning multiprompt test

```
curl -X POST "http://localhost:8080/flow" -H "Content-Type: application/json" -d '{"flow": "reasoning_multiprompt", "tenant": "ethz", "context": {"prompt_1": "Can you give me 20 mountain peaks over 5000m?", "prompt_2": "Now can you list these peaks by their height, in descending order?", "reasoning_effort": "low", "deployment": "Ethel_o4_mini"}, "stream": true}'
```
