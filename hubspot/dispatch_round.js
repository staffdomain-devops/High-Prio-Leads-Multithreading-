// HubSpot workflow custom code action -> GitHub Actions "Opportunity Call Round".
//
// Used by the single Opportunity workflow "[HPL-MT] Call sequence" (see docs/HUBSPOT_WORKFLOWS.md),
// once per round. Input field hs_object_id = the Opportunity. The pipeline itself decides the
// round (hpl_call_round); this script only decides whether to dispatch:
//   - no "Call N of 3 · " task on the Opportunity yet → round 0, dispatch with no task_id
//   - newest such task completed                      → dispatch with that task_id
//   - newest such task not completed                  → don't dispatch (e.g. the workflow's wait
//     timed out); dispatching then would replay the previous round's task window
//
// Secrets:
//   github_PAT            — GitHub fine-grained token, this repo only, "Contents: read & write"
//   sales_crm_api_token   — existing HubSpot private app token (tasks + custom object read)
// Runtime: Node 20.

const axios = require('axios');

const REPO = 'staffdomain-devops/High-Prio-Leads-Multithreading-';
const OPPORTUNITY = '2-42053860';

exports.main = async (event, callback) => {
  const opportunityId = String(event.inputFields.hs_object_id || '').trim();
  const hs = axios.create({
    baseURL: 'https://api.hubapi.com',
    headers: { Authorization: `Bearer ${process.env.sales_crm_api_token}` },
  });

  const { data } = await hs.get(`/crm/v4/objects/${OPPORTUNITY}/${opportunityId}/associations/tasks?limit=500`);
  const ids = (data.results || []).map(r => String(r.toObjectId));
  const tasks = [];
  for (let i = 0; i < ids.length; i += 100) {
    const res = await hs.post('/crm/v3/objects/tasks/batch/read', {
      properties: ['hs_task_subject', 'hs_task_status', 'hs_createdate'],
      inputs: ids.slice(i, i + 100).map(id => ({ id })),
    });
    tasks.push(...(res.data.results || []));
  }
  const task = tasks
    .filter(t => String(t.properties.hs_task_subject || '').includes('of 3 · '))
    .sort((a, b) => new Date(b.properties.hs_createdate) - new Date(a.properties.hs_createdate))[0];

  if (task && task.properties.hs_task_status !== 'COMPLETED') {
    return callback({ outputFields: { status: 'task_not_completed', task_id: task.id } });
  }
  const taskId = task ? task.id : '';

  // Display-only round for the Actions run name; the pipeline reads hpl_call_round itself.
  // Sent as a string so round "0" isn't treated as empty by the run-name expression.
  const opp = await hs.get(`/crm/v3/objects/${OPPORTUNITY}/${opportunityId}?properties=hpl_call_round`);
  const current = opp.data.properties.hpl_call_round;
  const round = current === null || current === undefined || current === '' ? '0' : String(Number(current) + 1);

  // 204 No Content on success; any error throws, and HubSpot retries the action
  await axios.post(
    `https://api.github.com/repos/${REPO}/dispatches`,
    { event_type: 'opp-round', client_payload: { opportunity_id: opportunityId, task_id: taskId, round } },
    {
      headers: {
        Authorization: `Bearer ${process.env.github_PAT}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    },
  );

  callback({ outputFields: { status: 'dispatched', task_id: taskId } });
};
