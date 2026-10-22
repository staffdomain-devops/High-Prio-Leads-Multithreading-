// HubSpot workflow custom code action -> GitHub Actions "Opportunity Call Round".
//
// Used by two workflows (see docs/HUBSPOT_WORKFLOWS.md):
//   W1 (Opportunity-based, round 0):        input field hs_object_id = the Opportunity
//   W3 (Task-based, call task completed):   input field hs_object_id = the Task
// The script works out which one it was given, finds the Opportunity, and posts
// a repository_dispatch. The pipeline itself decides the round (hpl_call_round).
//
// Secrets (Settings > Integrations > Private Apps / custom code secrets):
//   GITHUB_TOKEN  — fine-grained token, this repo only, "Contents: read & write"
//   HUBSPOT_TOKEN — private app token with tasks + Opportunity custom object read
// Input fields: hs_object_id. Output fields: status (string), opportunity_id (string).

const axios = require('axios');

const REPO = 'staffdomain-devops/High-Prio-Leads-Multithreading-';
const OPPORTUNITY = 'p5402982_opportunities';

exports.main = async (event, callback) => {
  const objectId = String(event.inputFields['hs_object_id']);
  const objectType = String(event.object.objectType || '').toUpperCase(); // 'TASK'/'0-27' or the Opportunity type

  let opportunityId = objectId;
  let taskId = '';

  if (objectType === 'TASK' || objectType === '0-27') {
    taskId = objectId;
    const hs = axios.create({
      baseURL: 'https://api.hubapi.com',
      headers: { Authorization: `Bearer ${process.env.HUBSPOT_TOKEN}` },
    });
    const { data } = await hs.get(`/crm/v4/objects/tasks/${taskId}/associations/${OPPORTUNITY}`);
    const first = (data.results || [])[0];
    if (!first) {
      return callback({ outputFields: { status: 'no_opportunity', opportunity_id: '' } });
    }
    opportunityId = String(first.toObjectId);
  }

  // 204 No Content on success; any error throws, and HubSpot retries the action
  await axios.post(
    `https://api.github.com/repos/${REPO}/dispatches`,
    { event_type: 'opp-round', client_payload: { opportunity_id: opportunityId, task_id: taskId } },
    {
      headers: {
        Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    },
  );

  callback({ outputFields: { status: 'dispatched', opportunity_id: opportunityId } });
};
