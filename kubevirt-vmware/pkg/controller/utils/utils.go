package utils

import (
	"fmt"
	"math/rand"
	"time"

	logf "sigs.k8s.io/controller-runtime/pkg/runtime/log"
)

var log = logf.Log.WithName("utils_v2vvmware")

const MaxRetryCount = 10

func SleepBeforeRetryN(delayBase int) {
	rand.Seed(time.Now().Unix())
	sleepTime := rand.Intn(5) + delayBase
	log.Info(fmt.Sprintf("Falling asleep for %d seconds before retry ...", sleepTime))
	time.Sleep(time.Second * time.Duration(sleepTime))
	log.Info("Awake after sleep, going to retry")
}

func SleepBeforeRetry() {
	SleepBeforeRetryN(3)
}
