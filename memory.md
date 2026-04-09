# Global Re-Identification Memory Management

The `GlobalReIDManager` employs a strategy to manage its in-memory gallery of vehicle identities, ensuring both performance and relevance. This involves loading recent state on startup and periodically cleaning up stale entries.

## State Loading on Startup

- **Persistence:** To maintain tracking continuity across application restarts, the system persists Re-ID identities (embeddings and metadata) to a database.
- **Hydration:** When the service starts, the `load_state` method is called. It fetches the `N` most recently seen identities from the database, where `N` is configured by `max_gallery_size`.
- **In-Memory Gallery:** These loaded identities are used to "hydrate" the in-memory gallery. The embeddings are loaded into a NumPy matrix (`gallery_matrix`) for efficient vector-based similarity searches, and the corresponding metadata (like `last_seen` timestamps) is loaded into the `metadata_store`. This allows the system to immediately start recognizing vehicles it has seen in the recent past, rather than starting with a "cold" empty gallery.

## Periodic Cleanup

- **TTL-Based Eviction:** To prevent the in-memory gallery from growing indefinitely and becoming populated with irrelevant data, a background cleanup process runs periodically.
- **`_cleanup` Method:** The `_cleanup` method is triggered if a certain amount of time has passed since the last cleanup.
- **Stale Check:** It iterates through all identities in the current gallery and checks their `last_seen` timestamp from the `metadata_store`.
- **Eviction:** If an identity has not been seen for longer than `ttl_seconds`, it is considered stale and is evicted. This involves:
    1. Removing the corresponding ID from `gallery_ids`.
    2. Removing the associated embedding vector from `gallery_matrix`.
    3. Deleting the entry from the `metadata_store`.
    4. Purging any `local_to_global` mappings that reference the expired global ID.
- **Memory Management:** This process ensures that the memory footprint of the gallery remains bounded and that searches are performed against a set of recent and relevant vehicle appearances.
