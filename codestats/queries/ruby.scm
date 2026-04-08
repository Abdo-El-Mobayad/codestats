; =============================================================================
; CodeStats -- Ruby symbol and import queries
; Adapted from Repowise (https://github.com/repowise-dev/repowise)
; =============================================================================

(method
  name: (identifier) @symbol.name
  parameters: (method_parameters)? @symbol.params
) @symbol.def

(singleton_method
  name: (identifier) @symbol.name
) @symbol.def

(class
  name: (constant) @symbol.name
) @symbol.def

(module
  name: (constant) @symbol.name
) @symbol.def

; require 'module' / require_relative './sibling'
(call
  method: (identifier) @_require_method
  arguments: (argument_list
    (string (string_content) @import.module)
  )
  (#match? @_require_method "^require")
) @import.statement
