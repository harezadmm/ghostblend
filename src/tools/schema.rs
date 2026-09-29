//! A tiny JSON-Schema builder and validator.
//!
//! We author schemas with these helpers instead of `#[derive(JsonSchema)]` so
//! every tool's contract is visible in one table and easy to audit. The
//! validator checks arguments before they reach Blender and returns messages
//! written for the agent that made the call.

use serde_json::{Map, Value, json};

/// Build an object schema. `props` is (name, schema) pairs; `required` names
/// the mandatory ones. Extra properties are always rejected.
pub fn object(props: &[(&str, Value)], required: &[&str]) -> Value {
    let mut map = Map::new();
    for (name, schema) in props {
        map.insert((*name).to_string(), schema.clone());
    }
    json!({
        "type": "object",
        "properties": Value::Object(map),
        "required": required,
        "additionalProperties": false,
    })
}

pub fn string(desc: &str) -> Value {
    json!({ "type": "string", "description": desc })
}

pub fn enum_str(desc: &str, values: &[&str]) -> Value {
    json!({ "type": "string", "description": desc, "enum": values })
}

pub fn boolean(desc: &str) -> Value {
    json!({ "type": "boolean", "description": desc })
}

pub fn number(desc: &str) -> Value {
    json!({ "type": "number", "description": desc })
}

pub fn number_range(desc: &str, min: f64, max: f64) -> Value {
    json!({ "type": "number", "description": desc, "minimum": min, "maximum": max })
}

pub fn integer(desc: &str) -> Value {
    json!({ "type": "integer", "description": desc })
}

pub fn integer_range(desc: &str, min: i64, max: i64) -> Value {
    json!({ "type": "integer", "description": desc, "minimum": min, "maximum": max })
}

/// A fixed-length numeric array, e.g. a location `[x, y, z]`.
pub fn vec_n(desc: &str, n: usize) -> Value {
    json!({
        "type": "array", "description": desc,
        "items": { "type": "number" }, "minItems": n, "maxItems": n,
    })
}

pub fn number_array(desc: &str) -> Value {
    json!({ "type": "array", "description": desc, "items": { "type": "number" } })
}

pub fn string_array(desc: &str) -> Value {
    json!({ "type": "array", "description": desc, "items": { "type": "string" } })
}

pub fn object_free(desc: &str) -> Value {
    json!({ "type": "object", "description": desc })
}

/// Validate `args` against `schema`. Returns every problem found, most useful first.
pub fn validate(schema: &Value, args: &Value) -> Result<(), Vec<String>> {
    let mut errors = Vec::new();
    validate_value(schema, args, "", &mut errors);
    if errors.is_empty() { Ok(()) } else { Err(errors) }
}

fn at(path: &str) -> String {
    if path.is_empty() { "the arguments".to_string() } else { format!("'{path}'") }
}

fn validate_value(schema: &Value, value: &Value, path: &str, errors: &mut Vec<String>) {
    let Some(ty) = schema.get("type").and_then(Value::as_str) else {
        return; // untyped schema: accept
    };
    match ty {
        "object" => validate_object(schema, value, path, errors),
        "array" => validate_array(schema, value, path, errors),
        "string" => validate_string(schema, value, path, errors),
        "number" | "integer" => validate_number(schema, value, ty, path, errors),
        "boolean"
            if !value.is_boolean() => {
                errors.push(format!("{} must be true or false", at(path)));
            }
        _ => {}
    }
}

fn validate_object(schema: &Value, value: &Value, path: &str, errors: &mut Vec<String>) {
    let Some(obj) = value.as_object() else {
        errors.push(format!("{} must be an object", at(path)));
        return;
    };
    let props = schema.get("properties").and_then(Value::as_object);
    if let Some(required) = schema.get("required").and_then(Value::as_array) {
        for r in required {
            if let Some(name) = r.as_str()
                && !obj.contains_key(name) {
                    errors.push(format!("{} is required in {}", quoted(name, path), at(path)));
                }
        }
    }
    let additional = schema
        .get("additionalProperties")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    for (key, v) in obj {
        match props.and_then(|p| p.get(key)) {
            Some(prop_schema) => validate_value(prop_schema, v, &child(path, key), errors),
            None if !additional => {
                let known: Vec<&str> = props
                    .map(|p| p.keys().map(String::as_str).collect())
                    .unwrap_or_default();
                let mut msg = format!("unknown parameter {}", quoted(key, path));
                if let Some(close) = closest(key, &known) {
                    msg.push_str(&format!("; did you mean '{close}'?"));
                } else if !known.is_empty() {
                    msg.push_str(&format!("; allowed: {}", known.join(", ")));
                }
                errors.push(msg);
            }
            None => {}
        }
    }
}

fn validate_array(schema: &Value, value: &Value, path: &str, errors: &mut Vec<String>) {
    let Some(arr) = value.as_array() else {
        errors.push(format!("{} must be an array", at(path)));
        return;
    };
    if let Some(min) = schema.get("minItems").and_then(Value::as_u64)
        && (arr.len() as u64) < min {
            errors.push(format!("{} needs at least {} item(s), got {}", at(path), min, arr.len()));
        }
    if let Some(max) = schema.get("maxItems").and_then(Value::as_u64)
        && (arr.len() as u64) > max {
            errors.push(format!("{} allows at most {} item(s), got {}", at(path), max, arr.len()));
        }
    if let Some(items) = schema.get("items") {
        for (i, item) in arr.iter().enumerate() {
            validate_value(items, item, &format!("{path}[{i}]"), errors);
        }
    }
}

fn validate_string(schema: &Value, value: &Value, path: &str, errors: &mut Vec<String>) {
    let Some(s) = value.as_str() else {
        errors.push(format!("{} must be a string", at(path)));
        return;
    };
    if let Some(allowed) = schema.get("enum").and_then(Value::as_array) {
        let allowed_strs: Vec<&str> = allowed.iter().filter_map(Value::as_str).collect();
        if !allowed_strs.contains(&s) {
            let mut msg = format!("{} must be one of: {}", at(path), allowed_strs.join(", "));
            if let Some(close) = closest(s, &allowed_strs) {
                msg.push_str(&format!(" (did you mean '{close}'?)"));
            }
            msg.push_str(&format!("; got '{s}'"));
            errors.push(msg);
        }
    }
}

fn validate_number(schema: &Value, value: &Value, ty: &str, path: &str, errors: &mut Vec<String>) {
    if ty == "integer" && !(value.is_i64() || value.is_u64()) {
        // A whole-valued float (2.0) is acceptable as an integer.
        let whole = value.as_f64().is_some_and(|f| f.fract() == 0.0);
        if !whole {
            errors.push(format!("{} must be a whole number", at(path)));
            return;
        }
    }
    let Some(n) = value.as_f64() else {
        errors.push(format!("{} must be a number", at(path)));
        return;
    };
    if let Some(min) = schema.get("minimum").and_then(Value::as_f64)
        && n < min {
            errors.push(format!("{} must be >= {}, got {}", at(path), trim(min), trim(n)));
        }
    if let Some(max) = schema.get("maximum").and_then(Value::as_f64)
        && n > max {
            errors.push(format!("{} must be <= {}, got {}", at(path), trim(max), trim(n)));
        }
}

fn child(path: &str, key: &str) -> String {
    if path.is_empty() { key.to_string() } else { format!("{path}.{key}") }
}

fn quoted(name: &str, path: &str) -> String {
    format!("'{}'", child(path, name))
}

fn trim(n: f64) -> String {
    if n.fract() == 0.0 { format!("{}", n as i64) } else { format!("{n}") }
}

/// Cheap edit-distance suggestion for a mistyped key or enum value.
fn closest<'a>(input: &str, candidates: &[&'a str]) -> Option<&'a str> {
    let input_l = input.to_lowercase();
    candidates
        .iter()
        .map(|c| (levenshtein(&input_l, &c.to_lowercase()), *c))
        .filter(|(d, c)| *d <= (c.len() / 2).max(2))
        .min_by_key(|(d, _)| *d)
        .map(|(_, c)| c)
}

fn levenshtein(a: &str, b: &str) -> usize {
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    let mut cur = vec![0usize; b.len() + 1];
    for (i, ca) in a.iter().enumerate() {
        cur[0] = i + 1;
        for (j, cb) in b.iter().enumerate() {
            let cost = if ca == cb { 0 } else { 1 };
            cur[j + 1] = (prev[j + 1] + 1).min(cur[j] + 1).min(prev[j] + cost);
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev[b.len()]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn schema() -> Value {
        object(
            &[
                ("name", string("object name")),
                ("size", number_range("edge length", 0.0, 100.0)),
                ("count", integer("how many")),
                ("location", vec_n("x y z", 3)),
                ("kind", enum_str("primitive kind", &["cube", "sphere"])),
            ],
            &["name"],
        )
    }

    #[test]
    fn accepts_valid_args() {
        let args = json!({"name": "Box", "size": 2, "location": [0, 0, 1], "kind": "cube"});
        assert!(validate(&schema(), &args).is_ok());
    }

    #[test]
    fn missing_required_field() {
        let errs = validate(&schema(), &json!({"size": 1})).unwrap_err();
        assert!(errs.iter().any(|e| e.contains("'name'") && e.contains("required")), "{errs:?}");
    }

    #[test]
    fn wrong_type() {
        let errs = validate(&schema(), &json!({"name": 5})).unwrap_err();
        assert!(errs.iter().any(|e| e.contains("'name'") && e.contains("string")), "{errs:?}");
    }

    #[test]
    fn out_of_range_number() {
        let errs = validate(&schema(), &json!({"name": "a", "size": 999})).unwrap_err();
        assert!(errs.iter().any(|e| e.contains("'size'") && e.contains("<= 100")), "{errs:?}");
    }

    #[test]
    fn wrong_vector_length() {
        let errs = validate(&schema(), &json!({"name": "a", "location": [0, 0]})).unwrap_err();
        assert!(errs.iter().any(|e| e.contains("'location'") && e.contains("3 item")), "{errs:?}");
    }

    #[test]
    fn unknown_field_names_it_and_suggests() {
        let errs = validate(&schema(), &json!({"name": "a", "nam": "b"})).unwrap_err();
        assert!(errs.iter().any(|e| e.contains("'nam'") && e.contains("did you mean 'name'")), "{errs:?}");
    }

    #[test]
    fn enum_miss_lists_allowed() {
        let errs = validate(&schema(), &json!({"name": "a", "kind": "cub"})).unwrap_err();
        assert!(errs.iter().any(|e| e.contains("cube, sphere") && e.contains("cube")), "{errs:?}");
    }

    #[test]
    fn integer_rejects_fraction() {
        let errs = validate(&schema(), &json!({"name": "a", "count": 1.5})).unwrap_err();
        assert!(errs.iter().any(|e| e.contains("'count'") && e.contains("whole")), "{errs:?}");
    }

    #[test]
    fn integer_accepts_whole_float() {
        assert!(validate(&schema(), &json!({"name": "a", "count": 3.0})).is_ok());
    }
}
