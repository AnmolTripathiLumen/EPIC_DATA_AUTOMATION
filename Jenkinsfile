try {
    library(
        identifier: 'jsl-jenkins-shared-library-local@stable',
        retriever: modernSCM([
            $class: 'GitSCMSource',
            remote: "/app/jenkins/git/jsl-jenkins-shared-library.git",
            extensions: [[$class: 'WipeWorkspace']]
        ])
    ) _
} catch (Exception Ex) {
    library(
        identifier: 'jsl-jenkins-shared-library@stable',
        retriever: modernSCM([
            $class: 'GitSCMSource',
            remote: "https://github.com/CenturyLink/jsl-jenkins-shared-library.git",
            credentialsId: 'SCMAUTO_GITHUB',
            extensions: [[$class: 'WipeWorkspace']]
        ])
    ) _
}

pipeline {
    environment {
        GITHUB_TOKEN_CREDENTIALS = 'GITHUB_APP_CREDENTIALS'
        GITHUB_SSH_CREDENTIALS = 'SCMAUTO_SSH_DEVOPS_PIPELINE'
        DOCKER_CREDENTIALS = 'mmgenai-nexus-secrets'
        QUALITY_GATE_CREDENTIALS = 'qualitygate-secret'
        JIRA_CREDENTIALS = 'jira-credentials'
        PROJECT_MAL = "MMGENAI"
        AUTHORIZED_USERS = 'authorized_users'
        DEPLOY_AUTH_TOKEN = 'deploy_auth_token'

        BRANCH_NAME = GIT_BRANCH.split('/')[-1].trim().toLowerCase()
        COMMIT_ID = GIT_COMMIT.substring(0,7).trim().toLowerCase()
        PULL_REQUEST = "pr-${env.CHANGE_ID}"

        DOCKER_REPO = 'mmgenai/epic-data-automation'
        GIT_SHA = env.GIT_COMMIT.substring(0, 7)
        IMAGE_TAG = "${env.GIT_SHA}-${env.BRANCH_NAME}-${BUILD_ID}"

        TEMPDIR = "${WORKSPACE}"

        DEPLOY_ENV = "${env.BRANCH_NAME}"
        DOCKER_REGISTRY = "nexusprod.corp.intranet:4567"
        NEXUSCRED = "nexuscred"
    }

    agent {
        label 'Docker-enabled'
    }

    options {
        timestamps()
        timeout(time: 1, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '10', daysToKeepStr: '30'))
        preserveStashes(buildCount: 10)
        disableConcurrentBuilds()
    }

    triggers {
        issueCommentTrigger('.test this please.')
        // Every Wednesday at 9:00 PM (server timezone)
        cron('0 21 * * 3')
    }

    parameters {
        choice(name: 'DEPLOY_ENV', choices: ['dev', 'qa', 'prod'], description: 'Select the deployment environment')
        string(name: 'IMAGE_TAG_OVERRIDE', defaultValue: '', description: 'Optional: Override the Docker image tag')
        booleanParam(name: 'SKIP_TESTS', defaultValue: false, description: 'Skip test stage')
        booleanParam(name: 'DEPLOY_SCHEDULER', defaultValue: true, description: 'Create/Update Cloud Scheduler (Wednesday 9pm)')
        booleanParam(name: 'RUN_JOB_NOW', defaultValue: false, description: 'Execute the Cloud Run Job immediately after deployment')
    }

    stages {
        stage('Init Parameters') {
            steps {
                script {
                    env.DEPLOY_ENV = params.DEPLOY_ENV ?: env.BRANCH_NAME
                    env.IMAGE_TAG = params.IMAGE_TAG_OVERRIDE?.trim() ? params.IMAGE_TAG_OVERRIDE.trim() : "${env.BRANCH_NAME}-${env.BUILD_ID}".toLowerCase()
                }
            }
        }

        stage('Load Properties') {
            steps {
                script {
                    def gcpProps = readProperties file: "cicd/jenkins/jenkins_config/jenkins_config_${params.DEPLOY_ENV}.properties"
                    env.GCP_CICD_CREDENTIALS = gcpProps['GCP_CICD_CREDENTIALS']
                    env.GCP_PROJECT = gcpProps['GCP_PROJECT']
                    env.AR_REGISTRY_HOST = gcpProps['AR_REGISTRY_HOST']
                    env.AR_DOCKER_REPO = gcpProps['AR_DOCKER_REPO']
                    env.AR_REGISTRY_CREDENTIALS = gcpProps['GCP_CICD_CREDENTIALS']
                    env.PROJECT_NAME = gcpProps['PROJECT_NAME']
                    env.IMAGE_NAME = "${env.PROJECT_NAME}"
                    env.GCS_BUCKET = gcpProps['GCS_BUCKET']
                    env.GCS_OUTPUT_PREFIX = gcpProps['GCS_OUTPUT_PREFIX']
                    env.JIRA_EMAIL_SECRET = gcpProps['JIRA_EMAIL_SECRET']
                    env.JIRA_TOKEN_SECRET = gcpProps['JIRA_TOKEN_SECRET']
                    env.SCHEDULER_NAME = gcpProps['SCHEDULER_NAME']
                    env.SCHEDULER_CRON = gcpProps['SCHEDULER_CRON']
                    env.SCHEDULER_TIMEZONE = gcpProps['SCHEDULER_TIMEZONE']
                    if (params.DEPLOY_ENV in ['qa', 'prod']) {
                        env.VPC_CONNECTOR = gcpProps['VPC_CONNECTOR']
                    }
                }
            }
        }

        stage('Authorize - Prod only') {
            when {
                expression { BRANCH_NAME ==~ /(production)/ }
            }
            steps {
                script {
                    jslDeploymentControlKnob()
                }
            }
        }

        stage('Create Images') {
            steps {
                println('StartCreateImage')
                script {
                    def dockerfile_path = 'Dockerfile'
                    jslDirectBuildAndPushToNexus(dockerfile_path)
                }
            }
        }

        stage('Copy image to Artifact Registry') {
            agent {
                label 'Docker-enabled'
            }
            options {
                timeout(time: 20, unit: 'MINUTES')
            }
            steps {
                script {
                    jslNexusToGcpCopy(IMAGE_NAME, IMAGE_TAG)
                }
            }
        }

        stage('Deploy Cloud Run Job') {
            agent {
                label "gcp-${params.DEPLOY_ENV}-deployment"
            }
            steps {
                script {
                    withCredentials([file(credentialsId: "${GCP_CICD_CREDENTIALS}", variable: 'GC_KEY')]) {
                        sh("""
                        set -euo pipefail
                        gcloud auth activate-service-account --key-file="\${GC_KEY}"
                        gcloud config set project "${GCP_PROJECT}"
                        """)
                    }

                    // Check if the Cloud Run Job exists
                    def checkJobExists = sh(script: """
                        set +e
                        echo "Cloud Run Job Deploy"
                        gcloud config set project ${GCP_PROJECT}
                        gcloud run jobs describe ${PROJECT_NAME} --region us-central1
                    """, returnStatus: true)
                    echo "checkJobExists code: ${checkJobExists}"

                    // Non-secret env vars
                    def envVars = "JIRA_DOMAIN=lumen.atlassian.net," +
                                  "GCS_BUCKET=${GCS_BUCKET}," +
                                  "GCS_OUTPUT_PREFIX=${GCS_OUTPUT_PREFIX}," +
                                  "ENVIRONMENT=${params.DEPLOY_ENV}"

                    // JIRA credentials from GCP Secret Manager (configurable per collaborator)
                    def secretVars = "JIRA_EMAIL=${JIRA_EMAIL_SECRET}:latest," +
                                     "JIRA_API_TOKEN=${JIRA_TOKEN_SECRET}:latest"

                    if (checkJobExists != 0) {
                        // Job does not exist -> create
                        if (params.DEPLOY_ENV == 'dev') {
                            sh("""
                                gcloud run jobs create "${PROJECT_NAME}" \
                                    --image="us-central1-docker.pkg.dev/${AR_DOCKER_REPO}/${PROJECT_NAME}:${IMAGE_TAG}" \
                                    --memory=2Gi \
                                    --cpu=2 \
                                    --task-timeout=7200s \
                                    --service-account="sa-aiops@${GCP_PROJECT}.iam.gserviceaccount.com" \
                                    --set-env-vars="${envVars}" \
                                    --set-secrets="${secretVars}" \
                                    --region=us-central1 \
                                    --project="${GCP_PROJECT}"
                            """)
                        } else if (params.DEPLOY_ENV in ['qa', 'prod']) {
                            sh("""
                                gcloud run jobs create "${PROJECT_NAME}" \
                                    --image="us-central1-docker.pkg.dev/${AR_DOCKER_REPO}/${PROJECT_NAME}:${IMAGE_TAG}" \
                                    --memory=2Gi \
                                    --cpu=2 \
                                    --task-timeout=7200s \
                                    --vpc-connector="${VPC_CONNECTOR}" \
                                    --vpc-egress=all-traffic \
                                    --service-account="sa-aiops@${GCP_PROJECT}.iam.gserviceaccount.com" \
                                    --set-env-vars="${envVars}" \
                                    --set-secrets="${secretVars}" \
                                    --region=us-central1 \
                                    --project="${GCP_PROJECT}"
                            """)
                        } else {
                            error "Unsupported DEPLOY_ENV: ${params.DEPLOY_ENV}"
                        }
                    } else {
                        // Job exists -> update
                        if (params.DEPLOY_ENV == 'dev') {
                            sh("""
                                gcloud run jobs update "${PROJECT_NAME}" \
                                    --image="us-central1-docker.pkg.dev/${AR_DOCKER_REPO}/${PROJECT_NAME}:${IMAGE_TAG}" \
                                    --memory=2Gi \
                                    --cpu=2 \
                                    --task-timeout=7200s \
                                    --service-account="sa-aiops@${GCP_PROJECT}.iam.gserviceaccount.com" \
                                    --update-env-vars="${envVars}" \
                                    --update-secrets="${secretVars}" \
                                    --region=us-central1 \
                                    --project="${GCP_PROJECT}"
                            """)
                        } else {
                            sh("""
                                gcloud run jobs update "${PROJECT_NAME}" \
                                    --image="us-central1-docker.pkg.dev/${AR_DOCKER_REPO}/${PROJECT_NAME}:${IMAGE_TAG}" \
                                    --memory=2Gi \
                                    --cpu=2 \
                                    --task-timeout=7200s \
                                    --vpc-connector="${VPC_CONNECTOR}" \
                                    --vpc-egress=all-traffic \
                                    --service-account="sa-aiops@${GCP_PROJECT}.iam.gserviceaccount.com" \
                                    --update-env-vars="${envVars}" \
                                    --update-secrets="${secretVars}" \
                                    --region=us-central1 \
                                    --project="${GCP_PROJECT}"
                            """)
                        }
                    }
                }
            }
        }

        stage('Setup Cloud Scheduler') {
            when {
                expression { return params.DEPLOY_SCHEDULER }
            }
            agent {
                label "gcp-${params.DEPLOY_ENV}-deployment"
            }
            steps {
                script {
                    withCredentials([file(credentialsId: "${GCP_CICD_CREDENTIALS}", variable: 'GC_KEY')]) {
                        sh("""
                        set -euo pipefail
                        gcloud auth activate-service-account --key-file="\${GC_KEY}"
                        gcloud config set project "${GCP_PROJECT}"
                        """)
                    }

                    // Cloud Scheduler: Every Wednesday at 9:00 PM
                    def jobUri = "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${GCP_PROJECT}/jobs/${PROJECT_NAME}:run"

                    def checkScheduler = sh(script: """
                        set +e
                        gcloud scheduler jobs describe ${SCHEDULER_NAME} \
                            --location=us-central1 \
                            --project=${GCP_PROJECT}
                    """, returnStatus: true)

                    if (checkScheduler != 0) {
                        echo "Creating Cloud Scheduler job..."
                        sh("""
                            gcloud scheduler jobs create http ${SCHEDULER_NAME} \
                                --location=us-central1 \
                                --project=${GCP_PROJECT} \
                                --schedule="${SCHEDULER_CRON}" \
                                --time-zone="${SCHEDULER_TIMEZONE}" \
                                --uri="${jobUri}" \
                                --http-method=POST \
                                --oauth-service-account-email="sa-aiops@${GCP_PROJECT}.iam.gserviceaccount.com"
                        """)
                    } else {
                        echo "Updating existing Cloud Scheduler job..."
                        sh("""
                            gcloud scheduler jobs update http ${SCHEDULER_NAME} \
                                --location=us-central1 \
                                --project=${GCP_PROJECT} \
                                --schedule="${SCHEDULER_CRON}" \
                                --time-zone="${SCHEDULER_TIMEZONE}" \
                                --uri="${jobUri}" \
                                --http-method=POST \
                                --oauth-service-account-email="sa-aiops@${GCP_PROJECT}.iam.gserviceaccount.com"
                        """)
                    }
                    echo "Scheduled: ${SCHEDULER_CRON} ${SCHEDULER_TIMEZONE} => Every Wednesday at 9:00 PM"
                }
            }
        }

        stage('Execute Job Now') {
            when {
                expression { return params.RUN_JOB_NOW }
            }
            agent {
                label "gcp-${params.DEPLOY_ENV}-deployment"
            }
            steps {
                script {
                    withCredentials([file(credentialsId: "${GCP_CICD_CREDENTIALS}", variable: 'GC_KEY')]) {
                        sh("""
                        set -euo pipefail
                        gcloud auth activate-service-account --key-file="\${GC_KEY}"
                        gcloud config set project "${GCP_PROJECT}"
                        gcloud run jobs execute ${PROJECT_NAME} \
                            --region=us-central1 \
                            --project="${GCP_PROJECT}" \
                            --wait
                        """)
                    }
                }
            }
        }
    }

    post {
        success {
            jslNotification('success')
            cleanWs()
        }
        failure {
            jslNotification('failure')
            cleanWs()
        }
        unstable {
            jslNotification('unstable')
            cleanWs()
        }
    }
}
