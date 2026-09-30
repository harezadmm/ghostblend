# Ghostblend — Desain v1

Tanggal: 2026-09-29 · Status: draft, menunggu approval sebelum implementasi

## 1. Ringkasan

**Ghostblend** adalah satu binary Rust yang sekaligus:

1. **MCP server** (stdio, protokol resmi lewat crate `rmcp` 3.5), dan
2. **supervisor** untuk satu proses **Blender headless** (`blender -b`) yang hidup terus di belakang layar sebagai worker.

Blender tetap Blender asli, tanpa modifikasi. Tidak ada addon yang harus dipasang, tidak ada GUI yang terbuka, tidak ada Python/uv yang harus diinstal pengguna. **Pengguna juga tidak perlu instal Blender:** Ghostblend membawa mesin Blender-nya sendiri dan mengunduh build portabel resmi saat pertama dipakai (lihat bagian 19). Pengguna cukup menjalankan `claude mcp add ghostblend -- ghostblend`.

Agent (Claude Code, Cursor, Codex, dan client MCP lain) mendapat tool bertipe untuk scene, objek, modifier, material, import/export, **preview visual yang dikembalikan sebagai gambar**, render final asinkron, **checkpoint/rollback**, validasi mesh, introspeksi API `bpy`, dan escape hatch `run_python`.

### Yang secara eksplisit BUKAN bagian proyek ini

- **Menulis ulang engine Blender dalam Rust.** Blender adalah 2,5 juta baris lebih C/C++ dengan 30 tahun pengembangan, lisensi GPL. Tidak realistis dan tidak perlu: nilai produk ini ada di lapisan "Blender sebagai backend untuk agent", bukan di engine.
- **Build Blender kustom** (`WITH_HEADLESS=ON` plus modul MCP yang di-link ke dalam Blender). Secara teknis mungkin, tapi biaya build-system-nya berminggu-minggu untuk keuntungan yang hampir nol dibanding pendekatan supervisor. Dicatat sebagai opsi v2 kalau suatu saat butuh satu file exe tanpa Blender terpisah.

## 2. Tujuan, pengguna, kriteria sukses

**Pengguna:** developer yang memakai AI coding agent untuk pekerjaan 3D: pipeline aset (konversi, decimate, LOD, bake), generasi prosedural dari teks, render produk/archviz, persiapan mesh untuk 3D print atau game engine.

**Masalah yang diselesaikan:** server MCP Blender yang ada berpusat pada GUI, dan mode headless-nya sekali pakai.

| Server | Cara kerja | Mode headless |
|---|---|---|
| Blender Lab MCP, server resmi. v1.0 rilis 27 April 2026, dipakai konektor Blender di Claude, GPL-3, butuh Blender 5.1+ | addon di Blender yang terbuka + proses relay Python | 6 tool `_for_cli`. Terverifikasi dari kode sumbernya: tiap panggilan menjalankan `subprocess.run([blender, "--background", blend_file, "--python-expr", ...])` dengan timeout 120 detik, lalu proses selesai. State tidak tersimpan antar panggilan kecuali kode menyimpan file sendiri. Tool yang mengembalikan gambar adalah screenshot jendela GUI. |
| MCP for Blender (ahujasid, 29k+ stars). Dulu `blender-mcp`, ganti nama 16 September 2026 supaya tidak dikira server resmi | addon di Blender yang terbuka + socket TCP | tidak ada |
| Proyek headless kecil: bpy.dev, Aurum, ellmos, sandraschi | beragam | "jalankan Python, tulis PNG ke path"; tanpa gambar di respons, tanpa rollback, tanpa job asinkron |

Celah yang diisi Ghostblend: sesi headless **persisten** tanpa bayar startup Blender sekitar 2 detik di tiap panggilan, preview yang kembali sebagai **gambar di respons MCP**, **autosave dan rollback**, **render asinkron**, pemulihan otomatis saat Blender crash, dan **satu binary** tanpa addon dan tanpa Python relay. Target Blender 4.2+.

**Kriteria sukses v1:**

| Kriteria | Target |
|---|---|
| Setup pengguna | satu perintah `claude mcp add`, tanpa addon, tanpa GUI |
| Startup worker sampai siap | di bawah 5 detik |
| Round-trip `render_preview` 512 px | di bawah 3 detik di mesin ini |
| Crash Blender | tidak pernah mematikan sesi MCP; worker restart otomatis dan scene dipulihkan dari autosave |
| Skenario acuan | agent membangun scene dari permintaan teks, melihat hasilnya, memperbaiki, export glTF, lalu render final, semuanya tanpa GUI |

## 3. Keputusan desain dan alasannya

| Keputusan | Alasan |
|---|---|
| Rust untuk server + supervisor | Satu binary statis, tanpa runtime, mudah didistribusikan; cocok untuk daemon yang harus mengawasi proses anak dengan timeout dan restart. |
| Blender tetap proses terpisah, bukan modul `bpy` di-import ke proses server | Modul pip `bpy` hanya bisa di-import sekali per proses, terkunci ke versi Python, dan crash-nya menjatuhkan server. Proses terpisah memberi isolasi crash dan menjaga kode Rust bebas dari kewajiban GPL. |
| Worker persisten, bukan spawn per panggilan | Startup Blender plus load scene terlalu lambat untuk loop iteratif agent. Persisten memberi state; spawn-per-call hanya dipakai untuk render final. |
| Komunikasi lewat stdin/stdout baris JSON dengan prefix `@@bhm:` | Tanpa port, tanpa firewall, tanpa addon. Prefix membedakan respons dari noise stdout Blender. Terverifikasi jalan di Blender 5.1.1 (lihat bagian 18). |
| Preview dikembalikan sebagai MCP image content, bukan hanya path | Agent headless itu buta. Gambar di respons memungkinkan loop lihat-perbaiki tanpa tool tambahan. |
| Autosave setelah setiap perintah mutatif | Mode background mematikan undo Blender. Simpan salinan `.blend` terukur 5 ms untuk scene kecil; ini memberi rollback dan crash recovery gratis. |
| Render final di proses Blender terpisah | `bpy.ops.render.render` memblokir main thread worker. Proses terpisah membuat render bermenit-menit tidak mengunci tool lain, dan gagal render tidak memengaruhi worker. |
| `ServerHandler` diimplementasikan manual (`list_tools` / `call_tool`), bukan macro `#[tool]` | Tabel tool dengan JSON Schema eksplisit lebih mudah diaudit, dan tahan terhadap perubahan API macro `rmcp` antar versi. |
| Workbench sebagai engine preview default | Terukur 0,9 detik untuk 512 px di mode background, tanpa perlu lighting. EEVEE dan Cycles tersedia sebagai opsi. |
| Nama `ghostblend` | "Ghost" = tanpa kepala, tak terlihat, bekerja di latar belakang. "Blend" = kata umum sekaligus ekstensi file `.blend`, jadi langsung terasa terkait Blender tanpa memakai merek "Blender". Blender adalah merek terdaftar Blender Foundation di EU dan AS, dan pemakaian komersial namanya dicadangkan untuk Foundation; roadmap hosted membuat ini relevan. Kompatibilitas cukup disebut di tagline: "Ghostblend: headless Blender for AI agents". Per 2026-09-29 nama ini kosong di crates.io, npm, dan PyPI, sehingga bisa didistribusikan lewat `cargo install`, `npx`, dan `uvx`; 0 repo GitHub; `ghostblend.dev` belum terdaftar. Nama lama `blenderd` ditinggalkan karena diawali merek Blender dan bertabrakan dengan paket Go `cernbox/blenderd`. |

## 4. Arsitektur

```
MCP client (Claude Code / Cursor / Codex)
        │  JSON-RPC over stdio
        ▼
┌─────────────────────────── ghostblend (Rust) ───────────────────────────┐
│ server.rs   ServerHandler: list_tools / call_tool → dispatch ke tools/  │
│ tools/      spesifikasi tool (nama, deskripsi, JSON Schema) + handler   │
│ bridge/     Worker: spawn blender -b, kirim request, baca respons,      │
│             timeout, restart + restore; bridge.py di-embed via          │
│             include_str! dan ditulis ke session dir saat start          │
│ jobs.rs     render final: satu proses blender per job, parse progress   │
│ session.rs  direktori sesi, ring autosave, checkpoint bernama           │
│ discover.rs cari blender.exe (flag > env > PATH > lokasi standar)       │
│ main.rs     CLI: serve (default) | doctor | --blender | --session-dir   │
└─────────────┬───────────────────────────────────────┬───────────────────┘
              │ stdin/stdout baris JSON                │ argv + stdout
              ▼                                        ▼
   blender.exe -b --python bridge.py           blender.exe -b snapshot.blend -f N
   (worker persisten, satu per sesi)           (proses per render final)
```

**Batas tanggung jawab:**

- `bridge.py` (Python, berjalan di dalam Blender): loop perintah, implementasi tiap perintah dengan `bpy`/`bmesh`, autosave, render preview ke file PNG, validasi mesh, introspeksi RNA. Tidak tahu apa-apa soal MCP.
- Rust: protokol MCP, skema tool, siklus hidup proses, timeout, job render, encoding gambar ke base64, logging. Tidak tahu apa-apa soal `bpy`.

## 5. Protokol Rust ⇄ Blender

Baris JSON, satu request satu respons, strictly sequential (worker single-thread).

Request (Rust → stdin Blender):

```json
{"id": 12, "cmd": "add_primitive", "args": {"type": "cube", "name": "Box"}}
```

Respons (stdout Blender → Rust), selalu diawali `@@bhm:`:

```json
@@bhm:{"id": 12, "ok": true, "result": {...}, "autosaved": "autosave/07.blend"}
@@bhm:{"id": 12, "ok": false, "error": {"type": "RuntimeError", "message": "...", "traceback": "...15 baris terakhir..."}}
```

Event tanpa `id`: `{"event": "ready", "blender": "5.1.1", "python": "3.13.9"}` saat worker siap, dan `{"event": "log", "level": "warn", "msg": "..."}` opsional.

Baris stdout lain (banner Blender, warning HIP, progress render) dianggap noise dan dicatat ke log level debug. Data biner tidak pernah lewat stdout: bridge menulis PNG ke direktori sesi dan mengembalikan path; Rust yang membaca file dan mengubahnya menjadi image content base64.

Encoding UTF-8 di kedua arah (`PYTHONIOENCODING=utf-8`, `PYTHONUNBUFFERED=1`, tulis lewat `sys.stdout.buffer` dan flush per baris).

## 6. Penemuan Blender dan peluncuran worker

Urutan pencarian: flag `--blender <path>` → env `GHOSTBLEND_BLENDER` → `blender` di PATH → Windows `C:\Program Files\Blender Foundation\Blender X.Y\blender.exe` (versi tertinggi) → macOS `/Applications/Blender.app/Contents/MacOS/Blender` → Linux `/usr/bin/blender`, snap, flatpak.

Di Windows wajib `blender.exe`, bukan `blender-launcher.exe`: launcher melepaskan diri dari console dan mematikan stdio.

Perintah worker:

```
blender.exe -b --factory-startup -noaudio --python <session>/bridge.py -- --session <session>
```

Versi minimum yang didukung: Blender 4.2 (API EEVEE Next dan `bpy` modern). Diuji di 5.1.1. `doctor` memperingatkan kalau versi di bawah 4.2.

## 7. Tools v1

"Mut" = mutatif, memicu autosave. Semua tool menerima objek JSON; semua mengembalikan teks JSON ringkas, kecuali `render_preview` dan `job_status` yang juga bisa mengembalikan image content.

| Tool | Mut | Argumen utama | Mengembalikan |
|---|---|---|---|
| `scene_new` | ya | `keep_defaults` (bool, default false) | ringkasan scene kosong |
| `scene_open` | ya | `path` | ringkasan scene |
| `scene_save` | – | `path` (opsional; default file saat ini) | path, ukuran |
| `scene_info` | – | – | objek (nama, tipe, lokasi, rotasi, skala, dimensi, verts/faces, material, parent, koleksi, visibilitas), kamera, lampu, world, frame range, engine, resolusi, unit, path file |
| `object_info` | – | `name` | detail: modifier, material dengan parameter principled, bbox dunia, statistik mesh, custom props |
| `add_primitive` | ya | `type` (cube, uv_sphere, ico_sphere, cylinder, cone, torus, plane, circle, monkey, grid, empty, camera, light), `name`, `location`, `rotation_deg`, `scale`, `size`, `radius`, `depth`, `segments`, `light_type`, `energy` | ringkasan objek baru |
| `transform` | ya | `name`, `location`, `rotation_deg`, `scale`, `mode` (set/delta), `apply` (bake transform ke mesh) | transform akhir |
| `object_delete` | ya | `names[]` | jumlah terhapus |
| `object_duplicate` | ya | `name`, `new_name`, `linked` | ringkasan objek baru |
| `modifier_add` | ya | `name`, `type` (subdivision, mirror, array, boolean, bevel, solidify, decimate, triangulate, weld, displace, remesh, wireframe, screw, smooth, edge_split), `params{}` divalidasi terhadap RNA, `apply` | daftar modifier objek |
| `modifier_apply` | ya | `name`, `modifier` (opsional: semua), `remove` (bool) | daftar modifier tersisa |
| `material_set` | ya | `object`, `name`, `base_color[4]`, `metallic`, `roughness`, `emission_color`, `emission_strength`, `alpha`, `transmission`, `texture_image_path`, `slot` | ringkasan material |
| `import_model` | ya | `path`, `format` (auto/gltf/fbx/obj/stl/usd/ply/abc), `options{}` | nama objek yang masuk, statistik |
| `export_model` | – | `path`, `format` (auto dari ekstensi), `selection[]`, `apply_modifiers`, `options{}` | path, ukuran, jumlah objek |
| `render_preview` | – | `views[]` (persp, front, back, left, right, top, bottom, camera; default 4 view jadi satu contact sheet), `size` (default 512), `engine` (auto/workbench/eevee/cycles), `objects[]` fokus, `shading` (solid/material/rendered), `wireframe`, `transparent` | **image content PNG** + path + info kamera per view |
| `render` | – | `output_path`, `frame` atau `frame_start`/`frame_end`, `engine`, `samples`, `resolution[2]`, `percentage`, `transparent`, `device` (gpu/cpu), `wait` (bool) | `job_id` (atau hasil langsung kalau `wait`) |
| `job_status` | – | `job_id`, `include_image` | state (queued/running/done/failed/cancelled), progress %, baris log terakhir, file output, opsional image content |
| `job_cancel` | – | `job_id` | state |
| `checkpoint_save` | – | `label` | id checkpoint |
| `checkpoint_list` | – | – | daftar checkpoint dan ring autosave |
| `checkpoint_restore` | ya | `id` atau `steps` (mundur N autosave) | ringkasan scene setelah restore |
| `validate` | – | `objects[]`, `checks[]` | per objek: non-manifold edges, loose verts, ngons, zero-area faces, skala negatif/belum di-apply, rotasi belum di-apply, tanpa UV, file gambar hilang, objek jauh dari origin; plus `ok` keseluruhan |
| `run_python` | ya | `code`, `timeout_s` | stdout, stderr, nilai variabel `result` (JSON, fallback repr) |
| `api_describe` | – | `path` (mis. `bpy.ops.mesh.primitive_cube_add`, `bpy.types.BevelModifier`) | properti dari RNA: nama, tipe, default, item enum, deskripsi |
| `api_search` | – | `query`, `kind` (ops/types/both), `limit` | nama yang cocok di `bpy.ops.*` dan `bpy.types.*` |

Total 25 tool. Deskripsi tool ditulis untuk agent: kapan memakai, unit (meter, derajat), dan contoh argumen.

## 8. Preview rendering

1. Hitung bounding box dunia dari objek target (default: semua mesh yang terlihat).
2. Buat kamera sementara per view. View sumbu memakai ortografik dengan `ortho_scale = 2 × radius × 1,1`; `persp` memakai perspektif dari arah depan-kanan-atas dengan jarak `radius / sin(fov/2) × 1,15`. `camera` memakai kamera scene.
3. Engine default Workbench (studio light, tanpa perlu lampu). `shading=material` memakai Workbench mode material; `shading=rendered` memakai EEVEE (atau Cycles kalau diminta) dan menambahkan lampu sementara bila scene tanpa lampu.
4. Setiap view dirender ke PNG. Kalau lebih dari satu view, digabung jadi contact sheet 2×2 lewat `bpy.data.images` dan numpy (bawaan Blender), lalu diberi label view di sudut.
5. Semua pengaturan render, kamera aktif, dan objek sementara dipulihkan setelah selesai. Tool ini tidak mutatif dan tidak memicu autosave.
6. Rust membaca PNG, mengembalikan `Content::image(base64, "image/png")` ditambah teks JSON berisi path dan info kamera.

## 9. Render final (job)

- Sebelum job dimulai, worker melakukan autosave ke `renders/<job_id>/scene.blend`.
- Rust menjalankan `blender.exe -b <scene.blend> --python-expr "<override engine/samples/resolusi/device>" -o <output> -f <frame>` (atau `-s/-e -a` untuk animasi) sebagai proses terpisah.
- Progress dibaca dari stdout: pola `Fra:N`, `Sample X/Y`, `Rendered X/Y Tiles`, dan `Saved: '...'` untuk selesai. Persentase dihitung dari sample atau frame.
- `job_status` melaporkan state dan bisa menyertakan gambar hasil. `job_cancel` mematikan proses. Maksimal 2 job berjalan bersamaan; sisanya antre.
- `wait=true` memblokir sampai selesai atau sampai batas 240 detik, lalu jatuh ke mode job.

## 10. Autosave, checkpoint, rollback

- Setelah setiap perintah mutatif, bridge menyimpan `save_as_mainfile(copy=True)` ke `autosave/NN.blend` (ring 10 file) dan `autosave/latest.blend`.
- `checkpoint_save` menyalin state ke `checkpoints/<id>-<label>.blend`.
- `checkpoint_restore` membuka file yang diminta lalu autosave lagi.
- Kalau satu autosave memakan lebih dari 2 detik, bridge mengirim event log peringatan; flag `--autosave off` mematikannya untuk scene raksasa.

## 11. Penanganan error, timeout, restart

- Argumen divalidasi terhadap JSON Schema sebelum sampai ke Blender; error validasi dikembalikan sebagai `isError` dengan pesan yang menyebut field yang salah.
- Exception di bridge → respons `ok:false` dengan tipe, pesan, dan 15 baris traceback terakhir; Rust meneruskannya sebagai `CallToolResult` `isError: true`.
- Timeout per perintah: default 120 detik; `render_preview` 300 detik; `run_python` mengikuti `timeout_s` maksimal 600 detik. Saat timeout, Rust mematikan worker, memulai ulang, memulihkan `autosave/latest.blend`, dan mengembalikan error yang menjelaskan itu.
- Worker mati (EOF stdout): panggilan berikutnya otomatis memulai worker baru dan memulihkan autosave terakhir; respons diberi catatan `"worker_restarted": true`.
- Render final berjalan di proses terpisah sehingga tidak pernah memengaruhi worker.

## 12. Sesi dan direktori

- Satu proses `ghostblend` = satu sesi = satu worker Blender. Client MCP yang berbeda menjalankan `ghostblend` masing-masing.
- Direktori sesi default `%LOCALAPPDATA%\ghostblend\s\<id 8 karakter>` (Windows) atau `~/.local/share/ghostblend/s/<id>` (Unix). Sengaja pendek: Blender gagal membuka skrip di path lebih dari kira-kira 250 karakter di Windows (terbukti saat spike).
- Isi: `bridge.py`, `autosave/`, `checkpoints/`, `previews/`, `renders/<job_id>/`, `ghostblend.log`.
- `--session-dir` mengganti lokasi; `--resume <id>` membuka sesi lama dari `autosave/latest.blend`. Direktori sesi tidak dihapus saat keluar karena berisi hasil render; `--ephemeral` menghapusnya.

## 13. Keamanan

- v1 hanya transport stdio: tingkat kepercayaan sama dengan shell agent itu sendiri.
- `run_python` adalah eksekusi kode penuh di mesin pengguna dan didokumentasikan begitu di deskripsi tool.
- Transport HTTP (`rmcp` `transport-streamable-http-server`) ditunda ke v2 bersama token auth dan isolasi proses per sesi.

## 14. Logging dan diagnosa

- stdout adalah kanal MCP, jadi semua log ke stderr lewat `tracing`, dikendalikan `RUST_LOG`, plus `--log-file`.
- `ghostblend doctor`: mencetak path dan versi Blender yang ditemukan, menjalankan worker uji, melaporkan engine yang tersedia, device Cycles (CUDA/OptiX/CPU), waktu startup, dan lokasi sesi.

## 15. Pengujian

- **Unit Rust:** urutan discovery dengan direktori dan env sementara; parser framing (`@@bhm:` di antara noise, JSON rusak, id tidak cocok); regex progress render; setiap skema tool valid (`type: object`, tanpa properti tanpa deskripsi).
- **Bridge di Blender asli:** `blender -b --python tests/bridge_test.py` memanggil fungsi dispatch langsung (tanpa stdin) dan memastikan `add_primitive`, `scene_info`, `validate`, `render_preview` menghasilkan nilai yang diharapkan. Dilewati kalau Blender tidak ada.
- **Integrasi Rust** (`#[ignore]` tanpa Blender): spawn worker sungguhan, jalankan `scene_new` → `add_primitive` → `render_preview`, pastikan PNG valid; matikan worker paksa, pastikan restart dan restore bekerja.
- **End-to-end MCP:** skrip Python bicara JSON-RPC lewat stdio ke binary hasil build: `initialize` → `tools/list` → `tools/call add_primitive` → `render_preview` mengembalikan image content.
- **Uji manual:** `claude mcp add ghostblend -- <path exe>` lalu skenario acuan di bagian 2.

## 16. Non-goals v1 dan roadmap

Bukan v1: transport HTTP/hosted, multi-sesi dalam satu proses, unduh Blender otomatis, tool khusus geometry nodes, tool animasi selain frame range dan render animasi, sculpt, texture paint, asset library.

Roadmap setelah v1:

- ~~**v1.1** unduh build portabel Blender ke direktori aplikasi, sehingga benar-benar nol instalasi.~~ Sudah dikerjakan pada 2026-09-29, lihat bagian 19.
- **v1.2** transport Streamable HTTP dengan token, image Docker dengan GPU (EGL di Linux), satu container per sesi.
- **v1.3** paket tool geometry nodes, simulasi, dan rigging. Sculpt, texture paint, keyframe, lighting, dan bake sudah masuk lebih dulu, lihat bagian 20.
- **v2** layanan hosted "Blender as a service untuk agent".

## 17. Rencana implementasi

1. Scaffold Cargo, CLI (`clap`), `discover.rs`, `doctor`.
2. `bridge.py`: loop perintah, `scene_new`, `scene_info`, `add_primitive`, `run_python`, autosave; `tests/bridge_test.py` dijalankan di Blender 5.1.
3. `bridge/worker.rs`: spawn, framing, timeout, restart + restore; unit test framing.
4. `server.rs` dengan `rmcp`: tabel tool + dispatch; uji end-to-end JSON-RPC.
5. `render_preview`: framing kamera, Workbench/EEVEE/Cycles, contact sheet, image content.
6. Tool sisanya: transform, delete, duplicate, modifier, material, import/export, validate, `api_describe`, `api_search`.
7. `jobs.rs`: render final terpisah, parsing progress, cancel, `wait`.
8. Checkpoint/rollback, README, instruksi `claude mcp add`, uji manual di Claude Code.

Perkiraan ukuran: sekitar 2.000 baris Rust dan 900 baris Python. Setiap langkah selesai dengan test yang lulus sebelum lanjut.

## 18. Hasil spike (bukti di mesin ini, 2026-09-29)

Mesin: Windows 11, RTX 4070, i5-13400F, Blender 5.1.1 (Python 3.13.9), Rust 1.97.1, `rmcp` 3.5.0.

| Uji | Hasil |
|---|---|
| Blender `-b` startup sampai skrip jalan | sekitar 2 detik |
| Render Workbench 512 px, mode background | 0,92 detik, PNG 99 KB |
| Render Cycles OptiX 16 sample 512 px | 1,93 detik, PNG 75 KB |
| Render EEVEE 8 sample 512 px | 2,25 detik, PNG 93 KB |
| `save_as_mainfile(copy=True)` scene kecil | 5 ms, 544 KB |
| Loop perintah JSON lewat stdin di mode background | jalan; 3 request 3 respons dengan prefix `@@bhm:`; proses keluar bersih saat `quit` |
| Path skrip lebih dari 250 karakter | Blender gagal membuka file → session dir harus pendek |
| `rmcp` 3.5.0 | tersedia `Content::image`, `stdio()`, `ServerHandler::{list_tools, call_tool, get_info}`, `ServiceExt::serve`, fitur `transport-streamable-http-server` untuk v2 |

## 19. Mesin Blender bawaan (ditambahkan 2026-09-29)

Permintaan pengguna: Ghostblend harus bisa dipakai tanpa instal Blender terpisah. Menulis ulang Blender tidak realistis, jadi Ghostblend mengadakan mesinnya sendiri: build portabel resmi Blender, tanpa modifikasi, yang dikelola Ghostblend.

| Keputusan | Alasan |
|---|---|
| Kunci ke Blender 5.1.2, walau 5.2 sudah rilis | Bridge teruji di seri 5.1; naik versi setelah diuji |
| SHA-256 dikunci di kode, diambil dari `blender-5.1.2.sha256` resmi | File rusak atau dimanipulasi ditolak dan dihapus |
| Lokasi `<data>/rt/5.1.2/` | Path pendek: file terdalam Blender 144 karakter, prefiks sekitar 50, jauh di bawah batas Windows |
| Marker `.ghostblend-ready` ditulis paling akhir | Ekstraksi yang terputus tidak pernah dipakai |
| Unduhan async dengan Range resume, deteksi macet 60 detik, 4 percobaan | Koneksi tidak stabil tetap selesai tanpa mengulang dari nol |
| Server tidak menunggu unduhan | Server langsung hidup; tool membalas progres sampai mesin siap |
| Blender terpasang 4.2+ tetap dipakai kalau mesin bawaan belum ada | Tidak mengunduh 414 MB bila tidak perlu; `--runtime managed` memaksa mesin bawaan |
| Folder `blender/` di samping executable dipakai lebih dulu | Distribusi offline cukup satu zip |

Urutan pencarian: `--blender` / `GHOSTBLEND_BLENDER`, folder `blender/` di samping executable, mesin bawaan, Blender terpasang 4.2+, lalu unduh. Didukung otomatis: Windows x64, Windows ARM64, Linux x64. macOS memakai Blender terpasang atau `--blender`.

Perintah baru: `ghostblend setup` untuk memasang mesin di depan dengan baris progres. Flag baru: `--runtime auto|managed|system`, `--no-download`. Env baru: `GHOSTBLEND_HOME` untuk memindahkan folder data.

Verifikasi di mesin ini:

| Uji | Hasil |
|---|---|
| 10 unit test runtime | lulus: hash, ekstraksi, path traversal, marker, urutan pencarian, unduh penuh, resume, macet, tolak hash salah, setup di latar belakang |
| `doctor --runtime managed` dengan mesin di folder data | memakai `rt/5.1.2/blender.exe`, worker siap 2,7 detik, preview 0,87 detik |
| E2E render stage dengan `--runtime managed` | 21 dari 21 lulus tanpa menyentuh instalasi sistem |
| Mesin tidak ada dan unduhan dimatikan | server tetap hidup dengan 25 tool; panggilan membalas instruksi `ghostblend setup` |

Belum diverifikasi di mesin ini: unduhan sungguhan 414 MB dari blender.org dan kompatibilitas bridge dengan 5.1.2. Linux diimplementasikan lewat `tar` sistem tetapi belum diuji.

## 20. Audit cakupan fitur Blender (ditambahkan 2026-09-30)

Permintaan pengguna: semua fitur Blender, dari modelling, sculpting, lighting, pewarnaan, sampai rendering, harus bisa dipakai lewat Ghostblend.

Metode: setiap area diuji langsung di Blender 5.1 mode background dengan efek yang bisa diukur (geometri berubah, piksel menyala, bone punya weight, file tertulis). Hasilnya disimpan sebagai tes regresi di `tests/bridge/test_capabilities.py`.

| Area | Hasil headless |
|---|---|
| Modelling | Operator Edit Mode, curve, teks, metaball, NURBS, geometry nodes, remesh voxel dan QuadriFlow, join: jalan. `loopcut_slide` crash. `knife_project` butuh viewport |
| Sculpting | Mode sculpt, Dyntopo, Multires: jalan. `sculpt.brush_stroke` butuh viewport. `sculpt.mesh_filter` crash |
| Lighting | Semua tipe lampu, HDRI (8 bawaan), emission, light linking: jalan |
| Pewarnaan | Material, tekstur gambar dan prosedural, warna vertex, UV unwrap semua metode, bake Cycles: jalan. Stroke paint butuh viewport |
| Rendering | Workbench, EEVEE, Cycles CPU dan OptiX, OIDN, DOF, motion blur, compositor, Freestyle, MP4, EXR multilayer: jalan. Render OpenGL tidak mungkin |
| Animasi dan rigging | Keyframe, driver, shape key, constraint, armature dengan weight otomatis: jalan |
| Simulasi | Rigid body, cloth, partikel, soft body, bake fluida Mantaflow: jalan |
| Lainnya | Grease Pencil v3, VSE, movie clip dan track, append dan asset: jalan |

Keputusan:

| Keputusan | Alasan |
|---|---|
| Tool `sculpt` dan `paint` dengan brush sendiri di atas bmesh dan data atribut | Operator stroke Blender menolak jalan tanpa viewport; brush sendiri deterministik dan bisa diuji |
| Tool `edit_mesh` dengan seleksi face berdasarkan arah, kotak, material, atau indeks | Agent tidak bisa mengeklik; seleksi deklaratif menggantikannya. `loop_cut` memakai `bmesh.ops.subdivide_edges` agar tidak menyentuh operator yang crash |
| `world_set`, `light_set`, `bake`, `animate` sebagai tool bertipe | Area yang paling sering dipakai; `run_python` tetap tersedia untuk sisanya |
| `run_python` memblokir `loopcut` dan `sculpt.mesh_filter` dengan error `HeadlessUnsupported` | Keduanya menjatuhkan proses Blender; lebih baik ditolak dengan petunjuk tool pengganti |
| Error poll atau viewport diberi petunjuk tool pengganti | Agent langsung tahu jalan lain |
| `render` menerima `format` png, jpeg, exr, exr_multilayer, mp4 | Blender 5.x mewajibkan `media_type` diatur sebelum `file_format` |
| `material_set` tanpa `name` mengedit material di slot | Sebelumnya membuat material baru dan menghapus hasil cat |
| `scene_new` memberi LineStyle ke lineset Freestyle | Scene kosong baru kehilangan linestyle sehingga Freestyle tidak menggambar apa pun |

Jumlah tool menjadi 32. Verifikasi di mesin ini: unit test Rust 48, integrasi supervisor 6, tes bridge 166, E2E 30 cek, clippy tanpa peringatan.
