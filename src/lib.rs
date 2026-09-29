//! Ghostblend: a headless Blender supervisor with a built-in MCP server.
//!
//! The binary in `main.rs` is a thin CLI over these modules; the library
//! surface also lets the integration and end-to-end tests drive the worker.

pub mod bridge_files;
pub mod config;
pub mod discover;
pub mod doctor;
pub mod jobs;
pub mod runtime;
pub mod server;
pub mod session;
pub mod tools;
pub mod util;
pub mod worker;
