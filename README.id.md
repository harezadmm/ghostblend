# Ghostblend

**Blender headless untuk AI agent, dengan MCP server bawaan.**

[English](README.md)

![Empat sudut pandang scene yang dibuat agent lewat Ghostblend, dikembalikan sebagai satu gambar](docs/images/preview-sheet.png)

*Agent membuat scene ini dengan sepuluh panggilan tool, lalu menerima empat
sudut pandang ini sebagai satu gambar dalam 1,5 detik. Tidak ada jendela yang
terbuka di layar.*

Ghostblend adalah satu file program. Ia berbicara dengan
[Model Context Protocol](https://modelcontextprotocol.io) lewat stdio, dan di
belakangnya berjalan Blender seutuhnya tanpa jendela. AI agent kamu mendapat
tool bertipe untuk membangun scene, mengedit mesh, memberi material, impor dan
ekspor model, serta render, dan agent bisa melihat hasil kerjanya sebagai
gambar. Kamu tidak perlu menginstal Blender, membukanya, atau memasang addon.

---

## Daftar isi

- [Kenapa Ghostblend dibuat](#kenapa-ghostblend-dibuat)
- [Apa yang bisa dilakukan agent](#apa-yang-bisa-dilakukan-agent)
- [Mulai cepat](#mulai-cepat)
- [Referensi tool](#referensi-tool)
- [Cara kerjanya](#cara-kerjanya)
- [Mesin Blender](#mesin-blender)
- [Konfigurasi](#konfigurasi)
- [Mengatasi masalah](#mengatasi-masalah)
- [Performa](#performa)
- [Keamanan](#keamanan)
- [Pengembangan](#pengembangan)
- [Status dan keterbatasan](#status-dan-keterbatasan)

---

## Kenapa Ghostblend dibuat

### Idenya

Orang yang bekerja dengan AI agent sering butuh *kemampuan* Blender: modelling,
modifier, material, konversi format, perbaikan mesh, render. Mereka tidak butuh
tampilan Blender. Agent memang tidak bisa memakai GUI. Jadi idenya sederhana:
jalankan Blender headless di latar belakang, lalu sambungkan ke agent lewat MCP,
cara standar agent berbicara dengan tool.

### Apa yang kurang dari solusi yang ada

Saat proyek ini dimulai, September 2026, pilihan untuk menyambungkan agent ke
Blender seperti ini:

| Pilihan | Cara kerja | Artinya bagi agent |
|---|---|---|
| MCP for Blender, server komunitas paling populer | Addon di dalam jendela Blender yang terbuka, diakses lewat socket lokal | Harus ada yang memasang addon dan membiarkan Blender terbuka di layar |
| MCP server resmi dari Blender Lab | Addon di dalam jendela Blender yang terbuka, ditambah proses relay; butuh Blender 5.1+ | Sama seperti di atas. Enam tool headless `_for_cli`-nya menyalakan Blender baru di tiap panggilan, jadi tidak ada yang tersimpan antar panggilan dan tiap panggilan menanggung waktu start Blender |
| Server headless kecil | Menjalankan string Python di `blender -b`, lalu menulis file | Agent buta, tidak ada undo, dan crash atau hang mengakhiri sesi |

Setiap pendekatan menyisakan minimal satu masalah berikut bagi agent:

- **Tidak bisa melihat.** Agent yang bekerja headless tidak punya viewport.
  Tanpa gambar, ia membangun dengan menebak-nebak.
- **Lupa.** Menyalakan Blender di tiap panggilan butuh sekitar dua detik dan
  membuang scene, kecuali agent menyimpan dan memuat ulang sendiri.
- **Tidak bisa undo.** Undo Blender tidak ada di mode background, jadi satu
  skrip yang salah merusak scene untuk semua panggilan berikutnya.
- **Macet.** Render panjang atau loop tak berujung memblokir semuanya, dan crash
  membawa hilang pekerjaan.
- **Repot dipasang.** Addon, jendela yang harus terbuka, environment Python.

### Jawaban Ghostblend

| Masalah | Jawaban Ghostblend |
|---|---|
| Tidak bisa melihat | `render_preview` mengembalikan gambar multi-sudut berlabel di dalam hasil tool |
| Lupa | Satu Blender tetap hidup sepanjang sesi; scene bertahan antar panggilan |
| Tidak bisa undo | Setiap perubahan scene di-autosave. Perubahan yang gagal otomatis di-rollback, dan `checkpoint_restore` bisa mundur berapa langkah pun |
| Macet | Python yang lepas kendali diinterupsi; hang atau crash membuat Blender di-restart dan autosave terakhir dipulihkan. Render final berjalan sebagai job terpisah di latar belakang |
| Repot dipasang | Satu file program. Ia mengunduh mesin Blender-nya sendiri saat pertama kali dipakai, atau memakai Blender yang sudah kamu punya |

### Prinsip desain

- **Blender tetap Blender.** Ghostblend tidak pernah menulis ulang atau
  memodifikasi Blender. Ia menjalankan build resmi tanpa perubahan sebagai
  subprocess. Menulis ulang program berukuran jutaan baris tidak mungkin dan
  tidak berguna; nilainya ada di lapisan antara agent dan Blender.
- **Agent tidak boleh kehilangan pekerjaan.** Autosave, rollback, dan pemulihan
  aktif secara bawaan.
- **Error harus mengajari.** Argumen divalidasi sebelum sampai ke Blender. Salah
  ketik seperti `widht` dibalas "did you mean 'width'?", dan nama objek yang
  tidak ada dibalas dengan daftar nama yang paling mirip.
- **Tidak boleh ada yang tertinggal.** Kalau server dimatikan paksa, semua proses
  Blender yang ia jalankan ikut berhenti dalam hitungan detik.

---

## Apa yang bisa dilakukan agent

Minta sesuatu dengan bahasa biasa, misalnya:

> Buat meja kecil berkaki empat dengan permukaan seperti kayu, tampilkan empat
> sudut pandangnya, cek apakah mesh-nya rapat, lalu ekspor sebagai `table.glb`.

Agent lalu memanggil tool seperti:

```text
scene_new
add_primitive    type=cube  name=Top   size=1  scale=[1.2, 0.8, 0.05]  location=[0, 0, 0.75]
add_primitive    type=cube  name=Leg1  size=1  scale=[0.05, 0.05, 0.72] location=[0.55, 0.35, 0.36]
object_duplicate name=Leg1  new_name=Leg2  location=[-0.55, 0.35, 0.36]
...
material_set     object=Top  base_color=[0.55, 0.35, 0.2, 1]  roughness=0.6
render_preview                          gambar kembali dan agent melihatnya
validate         objects=[Top, Leg1, ...]
export_model     path=table.glb
```

Pemakaian umum:

- **Pipeline aset:** konversi antar glTF, FBX, OBJ, STL, PLY, USD, dan Alembic;
  menerapkan modifier; decimate; validasi sebelum dikirim ke game engine.
- **Persiapan cetak 3D:** menemukan edge non-manifold, lubang, normal terbalik,
  dan skala yang belum di-apply, lalu ekspor STL.
- **Modelling prosedural dari teks:** membangun scene dari primitif dan modifier,
  lalu memperbaikinya sambil melihat preview.
- **Render produk dan konsep:** mengatur material dan lampu, lalu render dengan
  EEVEE atau Cycles di GPU kamu.
- **Apa pun yang bisa dilakukan Blender:** `run_python` memberi akses `bpy`
  penuh, dan `api_describe` / `api_search` membaca API Blender yang sedang
  berjalan, sehingga agent langsung memakai nama yang benar.

---

## Mulai cepat

### 1. Build

Kamu butuh [Rust toolchain](https://rustup.rs).

```bash
cargo build --release
```

Hasilnya `target/release/ghostblend` (`ghostblend.exe` di Windows).

### 2. Pasang mesin (opsional)

```bash
ghostblend setup
```

Perintah ini mengunduh Blender 5.1.2 (414 MB) dari download.blender.org,
memverifikasinya dengan SHA-256 yang dikunci di kode, lalu mengekstraknya ke
folder data Ghostblend. Langkah ini boleh dilewati: server melakukan hal yang
sama secara otomatis saat pertama kali butuh Blender. Kalau Blender 4.2 atau
lebih baru sudah terinstall, Ghostblend memakainya dan tidak mengunduh apa-apa.

### 3. Periksa

```bash
ghostblend doctor
```

```text
[ok] Engine in use: C:\Program Files\Blender Foundation\Blender 5.1\blender.exe
[ok] Blender version: 5.1.1
[ok] Worker ready in 1.86s (Blender 5.1.1, Python 3.13.9)
[ok] Render engines: BLENDER_WORKBENCH, BLENDER_EEVEE, CYCLES
[ok] Cycles GPU: NVIDIA GeForce RTX 4070 (CUDA), NVIDIA GeForce RTX 4070 (OPTIX)
[ok] render_preview (256px) in 0.74s
```

### 4. Sambungkan ke agent

**Claude Code**

```bash
claude mcp add ghostblend -- /path/to/ghostblend
```

**Claude Desktop, Cursor, dan client lain** memakai bentuk yang sama di file
konfigurasi MCP-nya (`claude_desktop_config.json`, `.cursor/mcp.json`, dan
seterusnya):

```json
{
  "mcpServers": {
    "ghostblend": {
      "command": "C:\\path\\to\\ghostblend.exe"
    }
  }
}
```

Tidak perlu argumen; `serve` adalah perintah bawaan. Path relatif yang dikirim
agent, misalnya `out/model.glb`, diarahkan ke folder tempat client menjalankan
Ghostblend.

### 5. Coba

Minta agent membuat sesuatu dan menampilkan preview-nya.

---

## Referensi tool

Jarak dalam meter, rotasi dalam derajat, warna berupa float RGBA dari 0 sampai 1.
Tool bertanda "autosave" mengubah scene. Kalau gagal, scene dikembalikan ke
kondisi sebelum panggilan.

### Scene

| Tool | Autosave | Fungsinya |
|---|---|---|
| `scene_new` | ya | Mulai scene kosong, atau kubus, kamera, dan lampu bawaan Blender dengan `keep_defaults` |
| `scene_open` | ya | Membuka file `.blend` |
| `scene_save` | | Menyimpan ke `.blend`; tanpa path, menyimpan kembali ke file yang terakhir dibuka atau disimpan |
| `scene_info` | | Semua objek beserta transform dan jumlah mesh, ditambah kamera, world, rentang frame, pengaturan render, dan satuan |
| `object_info` | | Detail lengkap satu objek: modifier dan pengaturannya, material, bounding box dunia, UV, custom property |

### Membangun

| Tool | Autosave | Fungsinya |
|---|---|---|
| `add_primitive` | ya | cube, uv_sphere, ico_sphere, cylinder, cone, torus, plane, circle, monkey, grid, empty, camera, light |
| `transform` | ya | Mengatur atau menggeser lokasi, rotasi, dan skala; `apply` membakar transform ke mesh |
| `object_delete` | ya | Menghapus objek berdasarkan nama; nama yang tidak ada dilaporkan, bukan dianggap error fatal |
| `object_duplicate` | ya | Menyalin objek, sebagai salinan mandiri atau instance yang berbagi mesh |
| `modifier_add` | ya | subdivision, mirror, array, boolean, bevel, solidify, decimate, triangulate, weld, displace, remesh, wireframe, screw, smooth, edge_split; `params` dicek terhadap pengaturan asli Blender |
| `modifier_apply` | ya | Membakar satu modifier, atau semuanya, ke mesh |
| `material_set` | ya | Principled BSDF: warna dasar, metallic, roughness, IOR, alpha, transmission, emission, tekstur gambar |

### File

| Tool | Autosave | Fungsinya |
|---|---|---|
| `import_model` | ya | glTF/GLB, FBX, OBJ, STL, PLY, USD, Alembic; format dari ekstensi |
| `export_model` | | Format yang sama; seluruh scene atau pilihan objek; modifier diterapkan secara bawaan |

### Melihat dan render

| Tool | Fungsinya |
|---|---|
| `render_preview` | Gambar cepat yang dikembalikan langsung: bawaannya lembar 2x2 berisi depan, kanan, atas, dan perspektif. `shading` bisa solid, textured, atau rendered; `objects` membingkai sebagian objek saja. Tidak pernah mengubah scene |
| `render` | Render final gambar diam atau animasi dengan Workbench, EEVEE, atau Cycles di GPU atau CPU. Berjalan sebagai job di latar belakang dan mengembalikan id job; `wait=true` menunggu untuk gambar diam yang singkat |
| `job_status` | Status, persen progres, baris log terakhir, file keluaran; `include_image` mengembalikan gambar jadinya |
| `job_cancel` | Menghentikan render yang antre atau sedang berjalan |

### Riwayat

| Tool | Autosave | Fungsinya |
|---|---|---|
| `checkpoint_save` | | Menyimpan snapshot bernama dari seluruh scene |
| `checkpoint_list` | | Checkpoint bernama ditambah riwayat autosave, satu entri per perubahan |
| `checkpoint_restore` | ya | Kembali ke checkpoint, ke autosave tertentu, atau `steps=N` perubahan ke belakang |

### Memeriksa dan scripting

| Tool | Autosave | Fungsinya |
|---|---|---|
| `validate` | | Edge non-manifold dan boundary, vertex lepas dan ganda, n-gon, face berluas nol, normal tidak konsisten, skala belum di-apply atau negatif, rotasi belum di-apply, UV hilang, tekstur hilang, objek terlalu jauh dari origin |
| `run_python` | ya | Menjalankan kode `bpy` di scene yang hidup; isi variabel `result` untuk mengembalikan JSON. Diinterupsi setelah `timeout_s` (bawaan 60) |
| `api_describe` | | Parameter, tipe, nilai bawaan, rentang, dan pilihan enum dari operator atau tipe, dibaca dari RNA Blender |
| `api_search` | | Mencari operator dan tipe berdasarkan kata kunci |

---

## Cara kerjanya

```text
AI agent (Claude Code, Cursor, ...)
        |  MCP: JSON-RPC lewat stdio
        v
+----------------------- ghostblend (Rust) -----------------------+
|  MCP server       skema tool, cek argumen, encode gambar        |
|  Supervisor       menyalakan Blender, satu panggilan per waktu, |
|                   timeout, restart dan pulihkan saat crash/hang |
|  Render job       satu Blender terpisah per render, progres     |
|  Pengelola mesin  mencari, atau mengunduh dan memverifikasi     |
+------------+-------------------------------------+--------------+
             | baris JSON lewat stdin/stdout       | per render job
             v                                     v
   blender -b --python bridge            blender -b scene.blend
   (satu worker yang terus hidup)        (berhenti saat job selesai)
```

**Sisi Rust** memegang semua urusan protokol dan proses: MCP server, JSON Schema
setiap tool, validasi argumen, timeout, restart, render job, encoding gambar,
serta mencari atau memasang mesin.

**Bridge Python** tertanam di dalam program dan ditulis ke folder per sesi saat
start. Ia berjalan di dalam Blender, membaca permintaan di thread latar belakang,
dan mengeksekusinya dengan `bpy` di main thread Blender. Ia tidak tahu apa-apa
soal MCP.

**Kebersihan protokol.** Balasan dikirim lewat salinan pribadi stdout asli
Blender. Semua hal lain yang dicetak Blender atau skrip agent dialihkan ke
stderr, jadi keluaran nyasar tidak akan pernah dikira balasan.

**Jaring pengaman.** Setelah setiap perintah yang mengubah scene, bridge
menyimpan salinan scene ke `autosave/NNNNNN.blend` dan menyimpan sepuluh yang
terakhir. Kalau perintah gagal, autosave sebelumnya dibuka lagi, jadi percobaan
ulang mulai dari kondisi bersih. Kalau Blender hang melewati batas waktu,
Ghostblend lebih dulu menginterupsi kode Python; kalau tidak berhasil, Blender
dimatikan, dinyalakan ulang, dan autosave terakhir dipulihkan. Proses lama selalu
sudah mati sebelum proses baru jalan, jadi dua Blender tidak pernah menulis file
yang sama.

**Render job** berjalan sebagai proses `blender -b` tersendiri terhadap
snapshot scene, jadi render panjang tidak memblokir pekerjaan agent yang lain.
Maksimal dua berjalan bersamaan, masing-masing dibatasi satu jam, dan
masing-masing mengawasi stdin-nya: saat Ghostblend berhenti, bahkan dimatikan
paksa, render ikut berhenti.

Desain lengkap beserta alasan setiap keputusan ada di
[`docs/superpowers/specs/2026-09-29-ghostblend-design.md`](docs/superpowers/specs/2026-09-29-ghostblend-design.md),
dan rencana pembangunannya di [`docs/superpowers/plans/`](docs/superpowers/plans/).

---

## Mesin Blender

Ghostblend butuh Blender untuk mengerjakan pekerjaan 3D yang sebenarnya, tapi
kamu tidak perlu menginstalnya. Ghostblend mencari mesin dengan urutan ini:

1. `--blender <path>` atau variabel `GHOSTBLEND_BLENDER`, kalau kamu mengaturnya.
   Pilihan ini tidak pernah diganti diam-diam dengan yang lain.
2. Folder `blender/` di samping program `ghostblend`, untuk paket offline.
3. Mesin milik Ghostblend di folder datanya.
4. Blender 4.2 atau lebih baru yang terinstall. Versi yang lebih lama dilewati.
5. Kalau tidak ada semuanya, Ghostblend mengunduh mesinnya sendiri.

### Mesin milik Ghostblend

| | |
|---|---|
| Versi | Blender 5.1.2, build portabel resmi, tanpa modifikasi |
| Sumber | download.blender.org |
| Verifikasi | SHA-256 dikunci di kode sumber, diambil dari checksum resmi Blender |
| Unduhan | 414 MB, sekali saja; unduhan yang terputus dilanjutkan dari titik berhentinya |
| Di disk | sekitar 1,2 GB |
| Lokasi | `%LOCALAPPDATA%\ghostblend\rt\5.1.2` di Windows, `~/.local/share/ghostblend/rt/5.1.2` di Linux |
| Platform | Windows x64, Windows ARM64, Linux x64 |

Selama mesin diunduh, server sudah berjalan. Panggilan tool dibalas dengan
progres seperti "downloading 43% (178 of 414 MB)", sehingga agent tahu harus
mencoba lagi sebentar kemudian. Koneksi yang macet terdeteksi setelah 60 detik
dan dicoba ulang sampai empat kali. File yang rusak gagal di pemeriksaan
checksum lalu dihapus. Mesin baru dianggap terpasang setelah selesai diekstrak
seluruhnya.

`--runtime managed` selalu memakai mesin milik Ghostblend, walaupun Blender
sudah terinstall. `--runtime system` hanya memakai Blender yang terinstall.
`--no-download` mematikan unduhan otomatis.

### Paket offline

Untuk memasang Ghostblend di komputer tanpa internet, ekstrak build portabel
resmi Blender ke folder bernama `blender` di samping program:

```text
ghostblend/
  ghostblend.exe
  blender/
    blender.exe
    5.1/ ...
```

---

## Konfigurasi

| Flag | Arti |
|---|---|
| `--blender <path>` | Pakai Blender ini, bukan mesin bawaan |
| `--runtime <auto\|managed\|system>` | Asal mesin (bawaan `auto`) |
| `--no-download` | Jangan pernah mengunduh mesin secara otomatis |
| `--workdir <dir>` | Folder dasar untuk path relatif (bawaan: folder saat ini) |
| `--session-dir <dir>` | Folder pasti untuk autosave, preview, dan render sesi ini |
| `--resume <id>` | Melanjutkan sesi sebelumnya dan memulihkan autosave terakhirnya |
| `--ephemeral` | Menghapus folder sesi saat server berhenti |
| `--autosave-keep <n>` | Jumlah autosave yang disimpan per sesi (bawaan 10) |
| `--max-jobs <n>` | Jumlah render job yang berjalan bersamaan (bawaan 2) |
| `--log-file <file>` | Menulis log ke file, bukan ke stderr |

| Variabel lingkungan | Arti |
|---|---|
| `GHOSTBLEND_BLENDER` | Sama dengan `--blender` |
| `GHOSTBLEND_HOME` | Memindahkan folder data (mesin dan sesi) |
| `GHOSTBLEND_LOG` | Tingkat log, misalnya `debug` |

Perintah: `ghostblend` atau `ghostblend serve` menjalankan MCP server,
`ghostblend setup` memasang mesin, dan `ghostblend doctor` memeriksa semuanya.

---

## Mengatasi masalah

Mulai dengan `ghostblend doctor`. Perintah ini menunjukkan mesin mana yang
dipakai dan apakah mesin itu bisa menyala dan render.

| Gejala | Yang perlu dilakukan |
|---|---|
| Tool bilang mesin sedang disiapkan | Tunggu unduhan pertama selesai, atau jalankan `ghostblend setup` di terminal untuk melihat progresnya |
| Unduhan berhenti | Jalankan `ghostblend setup` lagi; unduhan dilanjutkan |
| "Blender executable not found at ..." | Path di `--blender` atau `GHOSTBLEND_BLENDER` salah. Perbaiki, atau hapus supaya mesin bawaan dipakai |
| "cannot download its engine automatically on this platform" | Di macOS, instal Blender 4.2+ lalu pakai `--blender` |
| Perintah "timed out" | Blender sudah dinyalakan ulang dan scene kamu dipulihkan. Pakai `checkpoint_list` untuk melihat posisimu |
| Blender versi lama yang terpakai | Versi di bawah 4.2 dilewati. Pakai `--runtime managed` supaya selalu memakai mesin bawaan |
| Butuh detail | Jalankan dengan `GHOSTBLEND_LOG=debug`, atau `--log-file ghostblend.log` |

Folder sesi ada di `<data>/s/<id>`, di samping mesin. Masing-masing berisi
autosave, preview, dan hasil render bawaan dari satu kali server berjalan.

---

## Performa

Diukur di Windows 11 dengan RTX 4070 dan i5-13400F, Blender 5.1.

| Operasi | Waktu |
|---|---|
| Worker menyala, dari start sampai siap | 1,9 sampai 2,8 detik, sekali per sesi |
| Panggilan tool biasa (tambah objek, transform, material) | 10 sampai 20 ms |
| `render_preview`, satu sudut 256 px | 0,7 sampai 0,9 detik |
| `render_preview`, empat sudut 420 px | 1,5 detik |
| Autosave scene kecil | sekitar 5 ms |
| Ekspor scene kecil ke glTF | 0,4 detik |

---

## Keamanan

`run_python` menjalankan kode yang ditulis agent, dengan kekuatan penuh Python
Blender di komputer kamu, seperti tool scripting pada umumnya. Pakai Ghostblend
dengan agent dan input yang kamu percaya. Ghostblend hanya berbicara MCP lewat
stdio, jadi ia tidak bisa melakukan lebih dari yang sudah bisa dilakukan proses
agent itu sendiri; ia tidak membuka port jaringan. Unduhan mesin hanya menuju
download.blender.org dan diperiksa dengan hash yang dikunci.

---

## Pengembangan

```text
src/            Rust: MCP server, supervisor, render job, pengelola mesin
bridge/         Bridge Python yang berjalan di dalam Blender (ditanam saat build)
tests/bridge/   Tes bridge, dijalankan di dalam Blender
tests/e2e/      Tes end-to-end yang berbicara MCP ke program hasil build
docs/           Spec desain, rencana build, gambar
```

| Lapisan tes | Perintah | Jumlah |
|---|---|---|
| Unit test Rust | `cargo test --lib` | 48 |
| Supervisor dengan Blender asli | `cargo test --test worker_integration` | 6 |
| Bridge di dalam Blender | `blender -b --factory-startup --python tests/bridge/run_tests.py` | 74 |
| End-to-end lewat MCP | `python tests/e2e/mcp_e2e.py --binary target/release/ghostblend.exe --stage render` | 21 cek |

Tes end-to-end mencakup argumen yang salah, Blender yang tidak ada, panggilan
paralel, gambar preview langsung, render job, pembatalan, dan memastikan tidak
ada proses Blender yang tersisa saat Ghostblend dimatikan paksa.

---

## Status dan keterbatasan

Ghostblend ada di versi 0.1. Semua yang dijelaskan di atas sudah dibangun dan
diuji di Windows 11 dengan Blender 5.1. Kekurangan yang diketahui:

- Unduhan mesin otomatis sudah diuji dengan server uji lokal dan mesin hasil
  salinan, tapi belum sebagai unduhan penuh langsung dari blender.org.
- Pemasangan mesin di Linux sudah dibuat tapi belum diuji di Linux. macOS butuh
  Blender yang terinstall.
- Transport hanya stdio. Transport HTTP dengan autentikasi, untuk layanan
  hosted, sudah direncanakan.
- Satu proses server mengendalikan satu scene.
- Tool khusus untuk geometry nodes dan animasi sudah direncanakan; sampai saat
  itu, `run_python` bisa menanganinya.

## Lisensi

Lisensi untuk Ghostblend belum dipilih. Blender adalah program terpisah di bawah
GNU GPL. Ghostblend menjalankannya sebagai subprocess dan tidak berisi kodenya
sama sekali; mesinnya adalah build resmi tanpa modifikasi dari blender.org.
Kalau kamu mendistribusikan paket yang berisi Blender, sertakan file lisensi
Blender yang sudah ada di arsip resminya.
