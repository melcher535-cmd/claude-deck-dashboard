// Tests fuer die Sprachumschaltung. Aufruf: node test_i18n.js
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const { baueUebersetzer } = require("./i18n.js");

const test = (name, fn) => { fn(); console.log("ok  ", name); };
const platzhalter = s => [...s.matchAll(/\{(\d)\}/g)].map(m => m[1]).sort();
const ladePack = () => {
  const fenster = {};
  vm.runInNewContext(fs.readFileSync(__dirname + "/i18n-en.js", "utf8"), { window: fenster });
  return fenster.DECK_SPRACHEN.en;
};

test("translates_exact_text_and_keeps_surrounding_whitespace", () => {
  const t = baueUebersetzer({ exakt: { "Abmelden": "Sign out" } });
  assert.strictEqual(t("  Abmelden\n"), "  Sign out\n");
});

test("returns_unknown_text_unchanged", () => {
  const t = baueUebersetzer({ exakt: { "Abmelden": "Sign out" } });
  assert.strictEqual(t("Immich auf einer Ugreen NAS"), "Immich auf einer Ugreen NAS");
});

test("fills_placeholders_and_translates_the_captured_values", () => {
  const t = baueUebersetzer({ exakt: { "Reset in {0}": "Resets in {0}", "{0} Std {1} Min": "{0} h {1} min" } });
  assert.strictEqual(t("Reset in 4 Std 28 Min"), "Resets in 4 h 28 min");
});

test("allows_reordering_placeholders", () => {
  const t = baueUebersetzer({ exakt: { "{0} von {1}": "{1} of {0}" } });
  assert.strictEqual(t("3 von 9"), "9 of 3");
});

test("prefers_the_most_specific_template", () => {
  const t = baueUebersetzer({ exakt: { "{0} Nachrichten": "{0} messages", "Laufende {0} Nachrichten": "Running {0} messages" } });
  assert.strictEqual(t("Laufende 5 Nachrichten"), "Running 5 messages");
});

test("english_pack_is_consistent", () => {
  const pack = ladePack();
  assert.ok(pack.name && pack.locale);
  for (const [de, en] of Object.entries(pack.exakt)) {
    assert.ok(!/\{\d\}\{\d\}/.test(de), `adjacent placeholders are ambiguous: ${de}`);
    assert.deepStrictEqual(platzhalter(en), platzhalter(de), `placeholders differ: ${de}`);
    assert.ok(en.trim(), `empty translation: ${de}`);
  }
});

test("english_pack_translates_known_screens", () => {
  const t = baueUebersetzer(ladePack());
  assert.strictEqual(t("Sicherheitsstufe"), "Step-up check");
  assert.strictEqual(t("vor 5 Min"), "5 min ago");
  assert.strictEqual(t("Stand: vor 3 Min. Prozent, keine Token-Zahl."), "As of: 3 min ago. Percent, not a token count.");
});
