
const { IAM } = require('@google-cloud/iam');

async function setDefaultRole() {
  const projectId = process.env.GCLOUD_PROJECT;
  const member = `serviceAccount:${projectId}@gcp-sa-firebase.iam.gserviceaccount.com`;
  const role = 'roles/firebase.sdkAdmin';

  const iam = new IAM();

  const policy = await iam.getProjectIamPolicy({ project: projectId });

  policy.bindings.push({
    role: role,
    members: [member],
  });

  await iam.setProjectIamPolicy({ project: projectId, policy: policy });

  console.log(`Successfully set role ${role} for ${member} on project ${projectId}`);
}

setDefaultRole().catch(console.error);
