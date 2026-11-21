// Which model a settings control means (no browser, no engine):
//   node --experimental-strip-types --test "test/*.test.mjs"
import assert from "node:assert/strict";
import { test } from "node:test";

import { modelOnSwitch, modelToTest } from "../src/lib/models.ts";

// A provider that lists a catalogue, as Microsoft Foundry does: many names, one of them deployed here.
const catalogue = { id: "p_azure", models: ["AI21-Jamba-1.5-Large", "Cohere-command-r", "my-deployment", "Phi-4"] };
const one = { id: "p_local", models: ["llama3"] };
const none = { id: "p_new", models: [] };

test("Test asks the model the workspace uses with that provider, not the first one it lists", () => {
  const chosen = { default_provider_id: "p_azure", default_model: "my-deployment" };
  assert.equal(modelToTest(catalogue, chosen), "my-deployment");
  // the model for short jobs is a model in use too
  assert.equal(modelToTest(catalogue, { default_provider_id: "p_other", default_model: "x",
                                        utility_provider_id: "p_azure", utility_model: "Phi-4" }), "Phi-4");
  // and the default wins where a provider is both
  assert.equal(modelToTest(catalogue, { default_provider_id: "p_azure", default_model: "my-deployment",
                                        utility_provider_id: "p_azure", utility_model: "Phi-4" }), "my-deployment");
});

test("a provider the workspace does not use yet is tested on the first model it lists", () => {
  assert.equal(modelToTest(one, { default_provider_id: "p_azure", default_model: "my-deployment" }), "llama3");
  assert.equal(modelToTest(catalogue, {}), "AI21-Jamba-1.5-Large");
  assert.equal(modelToTest(catalogue, null), "AI21-Jamba-1.5-Large");
  // another provider's default model is not this provider's
  assert.equal(modelToTest(one, { default_provider_id: "p_other", default_model: "gpt-x" }), "llama3");
});

test("with no model named and none listed there is nothing to test, and nothing is invented", () => {
  assert.equal(modelToTest(none, {}), null);
  assert.equal(modelToTest(none, { default_provider_id: "p_new", default_model: "" }), null);
  assert.equal(modelToTest(none, { default_provider_id: "p_new", default_model: "   " }), null);
  assert.equal(modelToTest({ id: "p", models: null }, {}), null);
  assert.equal(modelToTest(null, { default_provider_id: "p", default_model: "m" }), null);
  // but a model the person typed is tested even though the provider lists nothing
  assert.equal(modelToTest(none, { default_provider_id: "p_new", default_model: "typed-by-hand" }), "typed-by-hand");
});

test("picking another provider keeps the model in use where that provider lists it", () => {
  assert.equal(modelOnSwitch(catalogue, "Phi-4"), "Phi-4");
  assert.equal(modelOnSwitch(catalogue, " Phi-4 "), "Phi-4");
});

test("a provider with one model has one answer; one with many, or none, is not guessed", () => {
  assert.equal(modelOnSwitch(one, "something-else"), "llama3");
  assert.equal(modelOnSwitch(one, null), "llama3");
  assert.equal(modelOnSwitch(catalogue, "gpt-x"), null, "the first of many is not the model anybody chose");
  assert.equal(modelOnSwitch(catalogue, null), null);
  assert.equal(modelOnSwitch(none, "gpt-x"), null, "and `default` is not a model");
  assert.equal(modelOnSwitch(null, "gpt-x"), null);
  assert.equal(modelOnSwitch({ id: "p", models: ["", "  "] }, null), null, "blank names are not models");
});
