# Paginated Word to editable PowerPoint V6

Use `run-word-to-ppt-workflow` as the sole public workflow and `run-pages` as the sole formal page-generation command. A one-page run uses `run-pages --pages 1`; there is no separate legacy single-page generation or prompt-compilation entry.

The workflow preserves complete page materials, applies the confirmed UI contract directly, optionally completes explicitly requested missing real assets once per project, and invokes one page director before the candidate loop. Each candidate receives the local technical check followed by the single independent semantic visual review. Rejections alone may trigger at most two corrections.

Every reviewed body candidate is the final dynamic 17:8 crop uniformly resized to 1904x896. Accepted images then become the sole visual input to editable reconstruction; fixed title, SVG Logo, footer and page number remain native layers outside Image2.
