//! The tool catalogue: one [`ToolSpec`] per MCP tool, its JSON Schema, and how
//! the server should carry it out ([`Kind`]).

pub mod defs;
pub mod schema;

use std::time::Duration;

use serde_json::Value;

/// How a tool call is fulfilled.
#[derive(Debug, Clone)]
pub enum Kind {
    /// Forward to the persistent worker under this bridge command name.
    Worker(&'static str),
    /// Render preview images (worker command `render_preview`, returns an image).
    Preview,
    /// Start or run a render job (job manager).
    Render,
    /// Query a render job's status.
    JobStatus,
    /// Cancel a render job.
    JobCancel,
}

#[derive(Debug, Clone)]
pub struct ToolSpec {
    pub name: &'static str,
    pub title: &'static str,
    pub description: &'static str,
    pub schema: Value,
    /// Mutates the scene: the worker autosaves and a failure rolls back.
    pub mutating: bool,
    /// Read-only hint for MCP clients.
    pub read_only: bool,
    pub timeout: Duration,
    pub kind: Kind,
}

impl ToolSpec {
    pub fn validate(&self, args: &Value) -> Result<(), Vec<String>> {
        schema::validate(&self.schema, args)
    }
}

/// The full catalogue, built fresh each call.
pub fn registry() -> Vec<ToolSpec> {
    defs::all()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn registry_has_32_unique_tools() {
        let reg = registry();
        assert_eq!(reg.len(), 32, "expected 32 tools");
        let names: HashSet<_> = reg.iter().map(|t| t.name).collect();
        assert_eq!(names.len(), 32, "tool names must be unique");
    }

    #[test]
    fn every_schema_is_a_closed_object_with_described_props() {
        for t in registry() {
            assert_eq!(t.schema["type"], "object", "{}: top-level schema must be object", t.name);
            assert_eq!(
                t.schema["additionalProperties"], false,
                "{}: must reject unknown properties", t.name
            );
            if let Some(props) = t.schema["properties"].as_object() {
                for (prop, sub) in props {
                    assert!(
                        sub.get("description").and_then(|d| d.as_str()).is_some_and(|s| !s.is_empty()),
                        "{}: property '{}' needs a description", t.name, prop
                    );
                }
            }
            // Required names must exist in properties.
            if let Some(req) = t.schema["required"].as_array() {
                let props = t.schema["properties"].as_object().unwrap();
                for r in req {
                    let name = r.as_str().unwrap();
                    assert!(props.contains_key(name), "{}: required '{}' not in properties", t.name, name);
                }
            }
        }
    }

    #[test]
    fn descriptions_are_present() {
        for t in registry() {
            assert!(!t.description.is_empty(), "{}: needs a description", t.name);
            assert!(!t.title.is_empty(), "{}: needs a title", t.name);
        }
    }
}
