//! Wire types for the Rust <-> Blender bridge protocol and the framing parser.
//!
//! The worker writes each protocol line as `\n@@bhm:<json>\n` on a private
//! duplicate of the original stdout. Everything Blender itself prints goes to
//! stderr, so in practice the protocol stream is clean, but we still scan for
//! the marker so a stray write can never be mistaken for a response.

use serde::Deserialize;
use serde_json::Value;

pub const MARKER: &str = "@@bhm:";

/// One decoded protocol message: either a reply to a request, or an event.
#[derive(Debug, Clone)]
pub enum Message {
    Response(Response),
    Event(Event),
    /// A line that carried the marker but could not be parsed as JSON.
    Malformed(String),
}

#[derive(Debug, Clone, Deserialize)]
pub struct Response {
    pub id: u64,
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub result: Value,
    #[serde(default)]
    pub error: Option<Value>,
    #[serde(default)]
    pub autosave: Option<Autosave>,
    #[serde(default)]
    pub rolled_back: Option<bool>,
    #[serde(default)]
    pub warnings: Option<Vec<String>>,
    #[serde(default)]
    pub ms: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Autosave {
    pub seq: u64,
    pub path: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Event {
    pub event: String,
    #[serde(default)]
    pub blender: Option<String>,
    #[serde(default)]
    pub python: Option<String>,
    #[serde(default)]
    pub pid: Option<u64>,
    #[serde(default)]
    pub seq: Option<u64>,
    #[serde(default)]
    pub level: Option<String>,
    #[serde(default)]
    pub msg: Option<String>,
    #[serde(default)]
    pub restored: Option<Autosave>,
    #[serde(default)]
    pub traceback: Option<String>,
}

/// Parse one line of worker output. Returns `None` for lines with no marker
/// (Blender banners, warnings, progress) so the caller can log them at debug level.
pub fn parse_line(line: &str) -> Option<Message> {
    let idx = line.find(MARKER)?;
    let json = line[idx + MARKER.len()..].trim();
    let Ok(value) = serde_json::from_str::<Value>(json) else {
        return Some(Message::Malformed(json.to_string()));
    };
    if value.get("event").is_some() {
        match serde_json::from_value::<Event>(value) {
            Ok(e) => Some(Message::Event(e)),
            Err(_) => Some(Message::Malformed(json.to_string())),
        }
    } else if value.get("id").is_some() {
        match serde_json::from_value::<Response>(value) {
            Ok(r) => Some(Message::Response(r)),
            Err(_) => Some(Message::Malformed(json.to_string())),
        }
    } else {
        Some(Message::Malformed(json.to_string()))
    }
}

/// Extract a short human-readable message from a bridge error object.
pub fn error_message(error: &Value) -> String {
    let kind = error.get("type").and_then(Value::as_str).unwrap_or("Error");
    let msg = error.get("message").and_then(Value::as_str).unwrap_or("(no message)");
    let mut out = format!("{kind}: {msg}");
    if let Some(hint) = error.get("hint").and_then(Value::as_str) {
        out.push_str("\nHint: ");
        out.push_str(hint);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn marker_found_after_noise() {
        let line = "Info: Saved blah blah @@bhm:{\"id\": 7, \"ok\": true, \"result\": {\"pong\": true}}";
        let msg = parse_line(line).unwrap();
        match msg {
            Message::Response(r) => {
                assert_eq!(r.id, 7);
                assert!(r.ok);
                assert_eq!(r.result["pong"], true);
            }
            other => panic!("expected response, got {other:?}"),
        }
    }

    #[test]
    fn lines_without_marker_are_ignored() {
        assert!(parse_line("Blender 5.1.1 (hash ...)").is_none());
        assert!(parse_line("Saved: 'C:/tmp/a.png'").is_none());
        assert!(parse_line("").is_none());
    }

    #[test]
    fn malformed_json_after_marker_is_reported_not_panicking() {
        let msg = parse_line("@@bhm:{not valid json").unwrap();
        assert!(matches!(msg, Message::Malformed(_)));
    }

    #[test]
    fn ready_event_parses() {
        let line = "@@bhm:{\"event\": \"ready\", \"blender\": \"5.1.1\", \"python\": \"3.13.9\", \"pid\": 42, \"seq\": 1}";
        match parse_line(line).unwrap() {
            Message::Event(e) => {
                assert_eq!(e.event, "ready");
                assert_eq!(e.blender.as_deref(), Some("5.1.1"));
                assert_eq!(e.pid, Some(42));
            }
            other => panic!("expected event, got {other:?}"),
        }
    }

    #[test]
    fn error_response_with_rollback_parses() {
        let line = "@@bhm:{\"id\": 3, \"ok\": false, \"error\": {\"type\": \"ValueError\", \"message\": \"boom\"}, \"rolled_back\": true, \"ms\": 12}";
        match parse_line(line).unwrap() {
            Message::Response(r) => {
                assert!(!r.ok);
                assert_eq!(r.rolled_back, Some(true));
                assert_eq!(error_message(r.error.as_ref().unwrap()), "ValueError: boom");
            }
            other => panic!("expected response, got {other:?}"),
        }
    }

    #[test]
    fn log_event_parses() {
        let line = "@@bhm:{\"event\": \"log\", \"level\": \"warn\", \"msg\": \"autosave slow\"}";
        match parse_line(line).unwrap() {
            Message::Event(e) => {
                assert_eq!(e.event, "log");
                assert_eq!(e.level.as_deref(), Some("warn"));
            }
            other => panic!("expected event, got {other:?}"),
        }
    }
}
