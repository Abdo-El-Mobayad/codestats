; =============================================================================
; CodeStats -- Python symbol and import queries
; tree-sitter-python >= 0.23
; Adapted from Repowise (https://github.com/repowise-dev/repowise)
;
; Capture name conventions (shared across ALL language query files):
;   @symbol.def       -- the full definition node (used for line numbers, kind)
;   @symbol.name      -- the name identifier node
;   @symbol.params    -- parameter list node (optional)
;   @symbol.modifiers -- decorator / visibility modifier nodes (optional)
;   @import.statement -- the full import node
;   @import.module    -- the module path being imported from
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Function (covers both regular and async)
(function_definition
  name: (identifier) @symbol.name
  parameters: (parameters) @symbol.params
) @symbol.def

; Class
(class_definition
  name: (identifier) @symbol.name
) @symbol.def

; Decorated function or class
(decorated_definition
  (decorator) @symbol.modifiers
  (function_definition
    name: (identifier) @symbol.name
    parameters: (parameters) @symbol.params
  ) @symbol.def
)

(decorated_definition
  (decorator) @symbol.modifiers
  (class_definition
    name: (identifier) @symbol.name
  ) @symbol.def
)

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

; from x.y import a, b
; from . import x
(import_from_statement
  module_name: (_) @import.module
) @import.statement

; import x.y.z
(import_statement
  name: (_) @import.module
) @import.statement
