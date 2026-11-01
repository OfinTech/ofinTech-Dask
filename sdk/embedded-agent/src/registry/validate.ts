/**
 * A small JSON-Schema (draft 2020-12 subset) validator: type, enum, required, properties, additionalProperties,
 * items, maxItems, minLength/maxLength, minimum/maximum. It mirrors the engine's `validate_args` so both ends agree.
 * Keywords it does not know are ignored, so a schema can only make it stricter.
 */

export interface Schema {
  type?: string | string[];
  enum?: unknown[];
  required?: string[];
  properties?: Record<string, Schema>;
  additionalProperties?: boolean;
  items?: Schema;
  maxItems?: number;
  minLength?: number;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  [key: string]: unknown;
}

function typeOf(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  return typeof value; // string | boolean | object | undefined
}

function matchesType(kind: string, value: unknown): boolean {
  const actual = typeOf(value);
  if (kind === "number") return actual === "number" || actual === "integer";
  if (kind === "integer") return actual === "integer";
  return kind === actual;
}

export function validate(schema: Schema, value: unknown, path = ""): string[] {
  const problems: string[] = [];
  const where = path || "props";
  const kinds = typeof schema.type === "string" ? [schema.type] : schema.type;
  if (kinds && !kinds.some((k) => matchesType(k, value))) {
    return [`${where} must be of type ${kinds.join(" or ")}.`];
  }
  if (schema.enum && !schema.enum.some((e) => e === value)) {
    return [`${where} must be one of: ${schema.enum.map(String).join(", ")}.`];
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) problems.push(`${where} must be at least ${schema.minimum}.`);
    if (schema.maximum !== undefined && value > schema.maximum) problems.push(`${where} must be at most ${schema.maximum}.`);
  }
  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) problems.push(`${where} is too short.`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) problems.push(`${where} is too long.`);
  }
  if (Array.isArray(value)) {
    if (schema.maxItems !== undefined && value.length > schema.maxItems) problems.push(`${where} has too many items.`);
    if (schema.items) value.forEach((item, i) => problems.push(...validate(schema.items as Schema, item, `${where}[${i}]`)));
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    const props = schema.properties ?? {};
    for (const key of schema.required ?? []) {
      if (!(key in obj)) problems.push(`${where}.${key} is required.`);
    }
    for (const [key, sub] of Object.entries(obj)) {
      const subSchema = props[key];
      if (subSchema) problems.push(...validate(subSchema, sub, `${where}.${key}`));
      else if (schema.additionalProperties === false && Object.keys(props).length) problems.push(`${where}.${key} is not a known property.`);
    }
  }
  return problems;
}
