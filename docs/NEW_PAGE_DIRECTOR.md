# New page director development

This change is the first milestone of a seven-PR delivery. It is not a release-ready claim.

The new page director reads the complete manuscript and the confirmed chapter, title, taskbook, page role and materials. It produces one `page_plan.image_prompt`, passed verbatim to Image2. The content inventory records source coverage; it is not a second layout plan. Original page titles remain source material while the confirmed derived title supplies the fixed title layer.

Initial generation and replanning call the same new director. Recovery validates its signed authority. The two former directors are not alternate modes or failure fallbacks; an old signed director record is not accepted as new-director evidence. Accepted body colors are preserved through editable reconstruction and final assembly.

Independent review, source integrity, object-level reconstruction checks and the shared initial-plus-two-corrections limit remain in force. Old test fixtures must be migrated to the new confirmed plan and source contract; restoring old runtime behavior to satisfy them is not an acceptable fix.

Milestones: (1) new director foundation; (2) deduplicated confirmed reads and one public skill; (3) shared full-source parsing; (4) unified run/resume; (5) real representative pages; (6) whole-deck delivery and bounded recovery; (7) verified installation, rollback and release. Each dependent PR must report its own actual checks. Offline checks cannot establish visual quality or final release readiness.
