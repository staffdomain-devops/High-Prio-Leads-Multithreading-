// HubSpot workflow custom code action -> enrol every Opportunity contact whose email for this
// round is written into that email's sequence ("HPL Email N").
//
// Used by "[HPL-MT] Call sequence" right after each "wait for HPL Call Round" step. Input field
// hs_object_id = the Opportunity. The slot comes from hpl_call_round (round 0 -> email 1,
// 1 -> 2, 2 -> 3, 3 -> 4). Email 5 is also written at round 3, but a contact can only be in one
// sequence at a time, so it gets its own copy of this action after a delay, with FORCE_SLOT = 5.
//
// A contact is enrolled only if:
//   - hplmt_email_N and hplmt_subject_N are populated (the pipeline clears dropped contacts' slots)
//   - hplmt_active_opportunity_id is this Opportunity or empty (round 3 releases it), not another one
//   - they haven't unsubscribed
//   - they haven't replied by email since the sequence started (same rule as scripts/replies.py:
//     incoming, after hpl_call_matrix.started_at, from the contact or in one of our threads,
//     not an auto-responder). The pipeline stops the whole sequence on a reply at the next
//     round; this check covers the gap before that, mainly email 5 which goes out after a
//     delay with no pipeline run in between.
// Each sends from the contact owner's inbox (falls back to the Opportunity owner).
//
// Secret:
//   sales_crm_api_token — needs automation.sequences.enrollments.write, automation.sequences.read,
//                         crm.objects.owners.read, sales-email-read, contacts + custom object read
// Runtime: Node 20 or Node 24.

const axios = require('axios');

const OPPORTUNITY = '2-42053860';
const SEQUENCES = { 1: '318840720', 2: '318819102', 3: '318817188', 4: '318824727', 5: '318824728' };
const FORCE_SLOT = null; // set to 5 on the email-5 copy of this action

// Keep in step with AUTO_REPLY_PATTERNS in scripts/replies.py
const AUTO_REPLY = new RegExp([
  'automatic reply', 'auto[- ]?reply', 'auto[- ]?response', 'automated (reply|response|message)',
  'out of (the )?office', 'away from (the |my )?(office|desk)', '\\booo\\b',
  'on (annual |parental |maternity |paternity |sick |extended )?leave',
  'undeliverable', 'delivery (status|failure|has failed)', 'mail delivery', 'failure notice',
  'returned mail', 'message not delivered',
].map(p => `(?:${p})`).join('|'), 'i');

// HubSpot's Node 20 runtime passes a callback; Node 24 expects the result to be returned.
// Returning it and calling the callback only when present works on both.
exports.main = async (event, callback) => {
  const result = await run(event);
  if (typeof callback === 'function') callback(result);
  return result;
};

async function run(event) {
  const opportunityId = String(event.inputFields.hs_object_id || '').trim();
  const hs = axios.create({
    baseURL: 'https://api.hubapi.com',
    headers: { Authorization: `Bearer ${process.env.sales_crm_api_token}` },
  });

  const opp = (await hs.get(`/crm/v3/objects/${OPPORTUNITY}/${opportunityId}`,
    { params: { properties: 'hpl_call_round,hubspot_owner_id,hpl_call_matrix' } })).data.properties;
  const round = opp.hpl_call_round;
  if (!FORCE_SLOT && (round === null || round === undefined || round === '')) {
    return { outputFields: { status: 'no_round_yet', enrolled: 0 } };
  }
  const slot = FORCE_SLOT || Number(round) + 1;
  const sequenceId = SEQUENCES[slot];
  if (!sequenceId) {
    return { outputFields: { status: `no_sequence_for_email_${slot}`, enrolled: 0 } };
  }
  let sinceMs = 0;
  try { sinceMs = Number(JSON.parse(opp.hpl_call_matrix || '{}').started_at) || 0; } catch (_) { sinceMs = 0; }

  async function associatedIds(fromType, fromId, toType) {
    const ids = [];
    let after;
    do {
      const { data } = await hs.get(`/crm/v4/objects/${fromType}/${fromId}/associations/${toType}`,
        { params: { limit: 500, after } });
      ids.push(...(data.results || []).map(r => String(r.toObjectId)));
      after = data.paging?.next?.after;
    } while (after);
    return ids;
  }

  async function batchRead(objectType, ids, properties) {
    const out = [];
    for (let i = 0; i < ids.length; i += 100) {
      const { data } = await hs.post(`/crm/v3/objects/${objectType}/batch/read`, {
        properties, inputs: ids.slice(i, i + 100).map(id => ({ id })),
      });
      out.push(...(data.results || []));
    }
    return out;
  }

  // Latest genuine reply from the contact since the sequence started, or null
  async function latestReply(contactId, contactEmail) {
    const ids = await associatedIds('contacts', contactId, 'emails');
    if (!ids.length) return null;
    const emails = (await batchRead('emails', ids,
      ['hs_email_direction', 'hs_email_subject', 'hs_email_from_email', 'hs_email_thread_id', 'hs_timestamp']))
      .map(e => e.properties);
    const ourThreads = new Set(emails
      .filter(e => e.hs_email_direction === 'EMAIL' && e.hs_email_thread_id)
      .map(e => e.hs_email_thread_id));
    const me = (contactEmail || '').trim().toLowerCase();
    const replies = emails.filter(e =>
      e.hs_email_direction === 'INCOMING_EMAIL' &&
      (Date.parse(e.hs_timestamp) || 0) >= sinceMs &&
      ((me && (e.hs_email_from_email || '').trim().toLowerCase() === me) || ourThreads.has(e.hs_email_thread_id)) &&
      !AUTO_REPLY.test(e.hs_email_subject || ''));
    if (!replies.length) return null;
    return replies.sort((a, b) => Date.parse(b.hs_timestamp) - Date.parse(a.hs_timestamp))[0];
  }

  const ids = await associatedIds(OPPORTUNITY, opportunityId, 'contacts');
  const contacts = await batchRead('contacts', ids, [
    'firstname', 'email', 'hubspot_owner_id', 'hs_email_optout', 'hplmt_active_opportunity_id',
    `hplmt_email_${slot}`, `hplmt_subject_${slot}`,
  ]);

  const senders = {};
  const ownerErrors = [];
  async function sender(ownerId) {
    if (!ownerId) return null;
    if (!(ownerId in senders)) {
      try {
        const { data } = await hs.get(`/crm/v3/owners/${ownerId}`);
        senders[ownerId] = data.userId && data.email ? { userId: data.userId, email: data.email } : null;
      } catch (err) {
        senders[ownerId] = null;
        ownerErrors.push(`owner ${ownerId}: HTTP ${err.response?.status || ''} ${err.response?.data?.message || err.message}`.trim());
      }
    }
    return senders[ownerId];
  }

  const enrolled = [];
  const skipped = [];
  const failed = [];
  const replyCheckErrors = [];
  for (const c of contacts) {
    const p = c.properties;
    const name = `${p.firstname || c.id}`;
    if (!p[`hplmt_email_${slot}`] || !p[`hplmt_subject_${slot}`]) { skipped.push(`${name}: no email_${slot}`); continue; }
    // Round 3 releases the lock in the same update that writes emails 4-5, so an empty lock is fine
    const lock = p.hplmt_active_opportunity_id;
    if (lock && lock !== opportunityId) { skipped.push(`${name}: in opportunity ${lock}'s sequence`); continue; }
    if (p.hs_email_optout === 'true') { skipped.push(`${name}: unsubscribed`); continue; }

    // A reply stops their emails. If the check itself fails (e.g. missing sales-email-read scope),
    // the send goes ahead and the error is reported, so one scope problem can't silence a round
    try {
      const reply = await latestReply(c.id, p.email);
      if (reply) {
        skipped.push(`${name}: replied ${reply.hs_timestamp} "${(reply.hs_email_subject || '').slice(0, 60)}"`);
        continue;
      }
    } catch (err) {
      replyCheckErrors.push(`${name}: HTTP ${err.response?.status || ''} ${err.response?.data?.message || err.message}`.trim());
    }

    const from = (await sender(p.hubspot_owner_id)) || (await sender(opp.hubspot_owner_id));
    if (!from) { failed.push(`${name}: no owner with an inbox`); continue; }

    const res = await hs.post('/automation/v4/sequences/enrollments',
      { sequenceId, contactId: c.id, senderEmail: from.email },
      { params: { userId: from.userId }, validateStatus: () => true });
    if (res.status >= 200 && res.status < 300) {
      enrolled.push(name);
    } else {
      failed.push(`${name}: HTTP ${res.status} ${res.data?.message || ''}`.trim());
    }
  }

  return {
    outputFields: {
      status: failed.length ? 'partial' : 'ok',
      email_slot: slot,
      sequence_id: sequenceId,
      enrolled: enrolled.length,
      enrolled_names: enrolled.join(', '),
      skipped: skipped.join(' | ').slice(0, 1000),
      failed: failed.join(' | ').slice(0, 1000),
      owner_errors: [...new Set(ownerErrors)].join(' | ').slice(0, 1000),
      reply_check_errors: replyCheckErrors.join(' | ').slice(0, 1000),
    },
  };
}
