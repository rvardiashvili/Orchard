# The Problem: Being an Apple User on Linux

## The Gap
For users deeply integrated into the Apple ecosystem (iPhone, iPad, Mac) who also rely on Linux workstations, the experience is jarring. While the hardware works, the data silo is real. 

Accessing your data usually means:
1.  **The Web Interface:** `icloud.com` is functional but slow, heavy, and disconnects you from your native desktop environment. You can't `grep` your notes, you can't open a Drive file in VS Code directly, and you can't script your Reminders.
2.  **One-Way Scripts:** There are excellent tools for downloading photos or backing up files, but they are generally "downloaders." They don't offer true two-way synchronization. You can't edit a file locally and expect it to seamlessly appear on your iPhone seconds later.
3.  **Third-Party Clouds:** Many users resign themselves to using Dropbox or Google Drive as a bridge, fracturing their digital life.

## Why Orchard?

I started Orchard because I didn't want a "viewer" for my iCloud data; I wanted it to live natively on my filesystem.

### The Technical Challenge
Building a sync engine is significantly harder than building a downloader. 
- **State vs. Data:** A filesystem is just data. To sync correctly, you need *state* (versions, ETags, dirty flags, conflict resolution).
- **Latency:** Blocking a file manager while downloading a 10MB PDF makes the system feel broken.
- **Correctness:** "Last write wins" isn't good enough. We need to handle offline edits, remote deletions, and conflicts safely.

### The Vision
Orchard is built differently. It ignores the standard approach of "mounting a remote URL" (like webdav) which is fragile and slow. Instead, it treats your local machine as a first-class citizen with its own database of truth (`OrchardDB`). 

It provides:
- **Optimistic UI:** Operations happen instantly locally and sync in the background.
- **Deep Integration:** Not just files. Notes become markdown. Reminders become structured lists. It attempts to map Apple's proprietary formats into open Linux standards.

Orchard exists to bridge the gap, making your Linux machine a fully capable peer in your iCloud device list.
