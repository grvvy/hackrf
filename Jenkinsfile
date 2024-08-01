pipeline {
    agent any
    stages {
        stage('Build Docker Image') {
            steps {
                sh 'docker build -t hackrf https://github.com/greatscottgadgets/hackrf.git'
            }
        }
        stage('Test Suite') {
            agent {
                docker {
                    image 'hackrf'
                    reuseNode true
                    args '--name hackrf_container --group-add=20 --group-add=46 --device-cgroup-rule="c 189:* rmw" --device-cgroup-rule="c 166:* rmw" --device /dev/bus/usb'
                }
            }
            steps {
                sh 'ls -la /dev/'
                sh 'sleep 5s'
                sh 'uhubctl --location 1-2.3 --port 3 --action off'
                sh 'sleep 5s'
                sh 'ls -la /dev/'
                sh 'uhubctl--location 1-2.3 --port 3 --action on'
                sh 'sleep 5s'
                sh 'ls -la /dev/'
                sh 'echo hello'
                sh 'echo hello'
                sh 'echo hello'
                sh 'echo hello'
            }
        }
    }
    post {
        always {
            cleanWs(cleanWhenNotBuilt: false,
                    deleteDirs: true,
                    disableDeferredWipeout: true,
                    notFailBuild: true)
        }
    }
}
