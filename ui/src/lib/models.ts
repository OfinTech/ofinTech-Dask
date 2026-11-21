// Which model a settings control means, decided apart from the page so that it can be tested without a browser.
//
// Both rules exist because of one assumption that stopped being true: that the first model a provider lists is a
// model the key can call. It holds for a provider that lists what it serves. It does not hold for one that lists
// a catalogue -- Microsoft Foundry answers `GET /models` with some five hundred names, of which a workspace has
// deployed one -- and there the first name is a model nobody deployed, so a test of it fails with "does not know
// this model" while the model the workspace actually uses answers.

export type ProviderLike = { id: string; models?: string[] | null };
export type Chosen = {
  default_provider_id?: string | null; default_model?: string | null;
  utility_provider_id?: string | null; utility_model?: string | null;
};

const named = (v: unknown): string => (typeof v === "string" ? v.trim() : "");
const listed = (p: ProviderLike | null | undefined): string[] =>
  (Array.isArray(p?.models) ? p!.models! : []).map(named).filter(Boolean);

/** The model a provider's Test button asks.
 *
 *  The one this workspace uses with that provider -- its default model, or its model for short jobs -- because
 *  that is the question the person pressing the button has. Only a provider the workspace does not use yet is
 *  tested on the first model it lists, and one that lists nothing cannot be tested until a model is named. */
export function modelToTest(p: ProviderLike | null | undefined, chosen: Chosen | null | undefined): string | null {
  if (!p) return null;
  if (p.id === chosen?.default_provider_id && named(chosen?.default_model)) return named(chosen?.default_model);
  if (p.id === chosen?.utility_provider_id && named(chosen?.utility_model)) return named(chosen?.utility_model);
  return listed(p)[0] ?? null;
}

/** The model to save when a person picks another provider, or null where they have to name it.
 *
 *  The model already in use is kept if the new provider lists it. A provider that lists exactly one model has
 *  only one answer. Anything else -- no list, or a list of many -- is not this function's to guess: picking the
 *  first of five hundred is how a workspace came to ask for a model it never deployed. */
export function modelOnSwitch(p: ProviderLike | null | undefined, current: string | null | undefined): string | null {
  const models = listed(p);
  const now = named(current);
  if (now && models.includes(now)) return now;
  return models.length === 1 ? models[0] : null;
}
