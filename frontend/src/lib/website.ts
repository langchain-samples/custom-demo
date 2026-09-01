/**
 * Guessing a customer's website from its name, for the create form's prefill.
 */

/**
 * A guess at the customer's domain, so "Home Depot" fills in "homedepot.com".
 *
 * Deliberately naive: strip everything that cannot appear in a domain label and add
 * ".com". That is right for most consumer brands and wrong for plenty of others
 * ("Q2 Holdings" is q2.com, not q2holdings.com), which is fine because it is a
 * PREFILL. It stops the moment you edit the field, and the setup agent treats the
 * website as a hint anyway.
 *
 * Corporate suffixes are dropped: nobody registers "acmeinc.com" for "Acme Inc".
 */
const CORP_SUFFIXES = /\s+(inc|llc|ltd|limited|corp|corporation|co|plc|gmbh|sa|ag|nv|holdings|group)\.?$/i;

export function guessWebsite(customer: string): string {
  let name = customer.trim();
  if (!name) return "";
  // Someone pasting a domain or URL already knows the answer; keep the host.
  const asUrl = name.match(/^(?:https?:\/\/)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?:[/?#]|$)/i);
  if (asUrl) return asUrl[1].toLowerCase().replace(/^www\./, "");
  // Strip one trailing corporate suffix, then everything a domain label cannot hold.
  name = name.replace(CORP_SUFFIXES, "");
  const label = name.toLowerCase().replace(/[^a-z0-9]/g, "");
  return label ? `${label}.com` : "";
}
