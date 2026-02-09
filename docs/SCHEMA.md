# SCHEMA（LLM 输入/输出 Schema）

## 1. LLM 输入 Schema
```json
{
  "type": "object",
  "properties": {
    "task": { "type": "string" },
    "context": { "type": "array", "items": { "type": "string" } },
    "constraints": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["task"]
}
```

## 2. LLM 输出 Schema
```json
{
  "type": "object",
  "properties": {
    "result": { "type": "string" },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
    "citations": { "type": "array", "items": { "type": "string" } }
  },
  "required": ["result"]
}
```

## 3. 工具/Skills 函数签名
- `tool_name(input: object) -> output: object`
- `skill_name.run(params: object) -> result: object`

## 4. 参数校验与错误返回
- 参数校验规则：
- 错误返回格式：
