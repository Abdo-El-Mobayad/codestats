"""Tests for codestats.vue_parser -- Vue SFC script block extraction."""

from __future__ import annotations

from codestats.vue_parser import extract_vue_scripts


def test_extract_vue_script_block():
    """Extracts <script> block content from Vue SFC."""
    source = """\
<template>
  <div>Hello</div>
</template>

<script>
export default {
  name: 'MyComponent',
  data() { return { count: 0 } }
}
</script>

<style scoped>
div { color: red; }
</style>
"""
    scripts = extract_vue_scripts(source)

    assert len(scripts) == 1
    assert "export default" in scripts[0]["content"]
    assert scripts[0]["lang"] == "js"
    assert scripts[0]["line_offset"] >= 0


def test_vue_typescript_lang():
    """Detects TypeScript lang attribute in <script> tag."""
    source = """\
<template><div></div></template>
<script lang="ts">
import { defineComponent } from 'vue';
export default defineComponent({ name: 'App' });
</script>
"""
    scripts = extract_vue_scripts(source)

    assert len(scripts) == 1
    assert scripts[0]["lang"] == "ts"


def test_vue_no_script_block():
    """Returns empty list for Vue files without <script> blocks."""
    source = """\
<template>
  <div>Template only</div>
</template>

<style>
div { color: blue; }
</style>
"""
    scripts = extract_vue_scripts(source)
    assert scripts == []
