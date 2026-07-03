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
        PULL_REQUEST="pr-${env.CHANGE_ID}"

        DOCKER_REPO = 'mmgenai/mmgenai'
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
        timeout(time: 2, unit: 'HOURS')
        buildDiscarder(logRotator(numToKeepStr: '10', daysToKeepStr: '30'))
        preserveStashes(buildCount: 10)
        disableConcurrentBuilds()
    }

    triggers {
        issueCommentTrigger('.test this please.')
    }

    parameters {
        choice(name: 'DEPLOY_ENV', choices: ['dev', 'qa', 'prod'], description: 'Select the deployment environment')
        string(name: 'IMAGE_TAG_OVERRIDE', defaultValue: '', description: 'Optional: Override the Docker image tag')
        booleanParam(name: 'SKIP_TESTS', defaultValue: false, description: 'Skip test stage')
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
                    env.VPC_CONNECTOR = gcpProps['VPC_CONNECTOR']

                    // Job-specific env vars for Cloud Run
                    env.JIRA_BASE_URL = gcpProps['JIRA_BASE_URL'] ?: 'https://lumen.atlassian.net'
                    env.JIRA_EMAIL = gcpProps['JIRA_EMAIL'] ?: 'Anmol.manitripathi@lumen.com'
                    env.BQ_TABLE_ID = gcpProps['BQ_TABLE_ID'] ?: 'prj-mm-genai-qa-001.All_Epic_Report.All_epics'
                    env.JIRA_SECRET_NAME = gcpProps['JIRA_SECRET_NAME'] ?: 'jira-api-token'
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

        stage('Deploy') {
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

                    def jobEnvVars = [
                        "JIRA_BASE_URL=${env.JIRA_BASE_URL}",
                        "JIRA_EMAIL=${env.JIRA_EMAIL}",
                        "GCP_PROJECT_ID=${env.GCP_PROJECT}",
                        "ENABLE_BIGQUERY_UPLOAD=true",
                        "ALL_EPICS_BIGQUERY_TABLE_ID=${env.BQ_TABLE_ID}",
                        "BQ_APPEND_PER_EPIC=true",
                        "BQ_CLEAR_TABLE_BEFORE_RUN=true",
                        "SAVE_COMBINED_EXCEL=false",
                        "SAVE_PER_EPIC_EXCEL=false",
                        "LOG_LEVEL=INFO",
                        "EPIC_PARALLEL_WORKERS=4",
                        "LLM_PARALLEL_WORKERS=8"
                    ].join(',')

                    def imageUrl = "us-central1-docker.pkg.dev/${AR_DOCKER_REPO}/${PROJECT_NAME}:${IMAGE_TAG}"
                    def serviceAccount = "sa-aiops@${GCP_PROJECT}.iam.gserviceaccount.com"

                    // Check if the Cloud Run Job exists
                    def checkJobExists = sh(script: """
                        set +e
                        gcloud run jobs describe ${PROJECT_NAME} --region us-central1
                    """, returnStatus: true)
                    echo "checkJobExists code: ${checkJobExists}"

                    if (checkJobExists != 0) {
                        // Job does not exist → create
                        sh("""
                            gcloud run jobs create "${PROJECT_NAME}" \
                            --image="${imageUrl}" \
                            --memory=2Gi \
                            --cpu=2 \
                            --task-timeout=3600s \
                            --max-retries=1 \
                            --vpc-connector="${VPC_CONNECTOR}" \
                            --vpc-egress=all-traffic \
                            --service-account="${serviceAccount}" \
                            --set-env-vars="${jobEnvVars}" \
                            --set-secrets="JIRA_API_TOKEN=${env.JIRA_SECRET_NAME}:latest" \
                            --region=us-central1 \
                            --project="${GCP_PROJECT}"
                        """)
                    } else {
                        // Job exists → update
                        sh("""
                            gcloud run jobs update "${PROJECT_NAME}" \
                            --image="${imageUrl}" \
                            --memory=2Gi \
                            --cpu=2 \
                            --task-timeout=3600s \
                            --max-retries=1 \
                            --vpc-connector="${VPC_CONNECTOR}" \
                            --vpc-egress=all-traffic \
                            --service-account="${serviceAccount}" \
                            --set-env-vars="${jobEnvVars}" \
                            --set-secrets="JIRA_API_TOKEN=${env.JIRA_SECRET_NAME}:latest" \
                            --region=us-central1 \
                            --project="${GCP_PROJECT}"
                        """)
                    }
                }
            }
        }
    }

    post {
        success {
            script {
                try { jslNotification('success') } catch (e) { echo "Notification skipped: ${e.message}" }
            }
            cleanWs()
        }
        failure {
            script {
                try { jslNotification('failure') } catch (e) { echo "Notification skipped: ${e.message}" }
            }
            cleanWs()
        }
        unstable {
            script {
                try { jslNotification('unstable') } catch (e) { echo "Notification skipped: ${e.message}" }
            }
            cleanWs()
        }
    }
}
