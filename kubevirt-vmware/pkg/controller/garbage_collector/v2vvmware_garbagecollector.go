package garbage_collector

import (
	"context"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"

	kubevirtv1alpha1 "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/apis/kubevirt/v1alpha1"
	"github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/utils"

	"github.com/go-logr/logr"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/manager"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	logf "sigs.k8s.io/controller-runtime/pkg/runtime/log"
	"sigs.k8s.io/controller-runtime/pkg/source"
)

const vCenterTemporaryLabel = "cnv.io/temporary"

const DefaultTimeToLiveDuration = time.Second * 20 // TODO: increase

var doneResult = reconcile.Result{} // no requeue
var rescheduleResult = reconcile.Result{RequeueAfter: time.Second*5} // TODO: increase

var log = logf.Log.WithName("gc_v2vvmware")

// GC creates a new V2VVmware Garbage Collector (controller) and adds it to the Manager. The Manager will set fields on the Controller
// and Start it when the Manager is Started.
func GC(mgr manager.Manager) error {
	return addGc(mgr, newReconciler(mgr))
}

// newReconciler returns a new reconcile.Reconciler
func newReconciler(mgr manager.Manager) reconcile.Reconciler {
	return &ReconcileV2VVmware{client: mgr.GetClient(), scheme: mgr.GetScheme()}
}

// add adds a new Controller to mgr with r as the reconcile.Reconciler
func addGc(mgr manager.Manager, r reconcile.Reconciler) error {
	// Create a new controller
	c, err := controller.New("v2v-vmware-garbage-collector", mgr, controller.Options{Reconciler: r})
	if err != nil {
		return err
	}

	// Watch for changes to primary resource V2VVmware
	err = c.Watch(&source.Kind{Type: &kubevirtv1alpha1.V2VVmware{}}, &handler.EnqueueRequestForObject{})
	if err != nil {
		return err
	}

	return nil
}

var _ reconcile.Reconciler = &ReconcileV2VVmware{}

type ReconcileV2VVmware struct {
	client client.Client
	scheme *runtime.Scheme
}

func (r *ReconcileV2VVmware) updateDeletionTimestamp(namespacedName types.NamespacedName, valueTime time.Time, counter int) error {
	value := valueTime.Format(time.RFC3339)
	obj := &kubevirtv1alpha1.V2VVmware{}
	err := r.client.Get(context.TODO(), namespacedName, obj) // get a fresh copy
	if err != nil {
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateDeletionTimestamp(namespacedName, valueTime, counter - 1)
		}
		return err
	}

	obj.Spec.TimeToLive = value
	err = r.client.Update(context.TODO(), obj)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update V2VVmware timeToLive. Intended to write: '%s'", value))
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateDeletionTimestamp(namespacedName, valueTime, counter - 1)
		}
	}
	return nil
}

func (r *ReconcileV2VVmware) updateSecretDeletionTimestamp(namespacedName types.NamespacedName, valueTime time.Time, counter int) error {
	value := valueTime.Format(time.RFC3339)
	obj := &corev1.Secret{}
	err := r.client.Get(context.TODO(), namespacedName, obj) // get a fresh copy
	if err != nil {
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateSecretDeletionTimestamp(namespacedName, valueTime, counter - 1)
		}
		return err
	}

	obj.Data["timeToLive"] = []byte(value)
	err = r.client.Update(context.TODO(), obj)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update Secret timeToLive. Intended to write: '%s'", value))
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateSecretDeletionTimestamp(namespacedName, valueTime, counter - 1)
		}
	}
	return nil
}


func (r *ReconcileV2VVmware) pruneV2VVMwares(reqLogger logr.Logger, namespace string ) reconcile.Result {
	result := doneResult

	opts := &client.ListOptions{
		Namespace: namespace,
	}

	v2vvmwares := &kubevirtv1alpha1.V2VVmwareList{}
	err := r.client.List(context.TODO(), opts, v2vvmwares)
	if err != nil {
		reqLogger.Error(err, "Failed to get list of temporary V2VVMWare objects.")
		return rescheduleResult
	}

	log.Info(fmt.Sprintf("List of V2VVMWare objects retrieved, count: %d", len(v2vvmwares.Items)))
	for _, obj := range v2vvmwares.Items {
		if len(obj.Spec.TimeToLive) > 0 { // timeToLive is set
			result = rescheduleResult
			reqLogger.Info(fmt.Sprintf("Object with timeToLive found, name = '%s', value = '%s', now = '%s'", obj.Name, obj.Spec.TimeToLive, time.Now().Format(time.RFC3339)))
			timeToLive, _ := time.Parse(time.RFC3339, obj.Spec.TimeToLive)

			if time.Now().After(timeToLive) {
				reqLogger.Info(fmt.Sprintf("Time to live is gone for V2VVmware object '%s', ttl = '%s'. Will be removed", obj.Name, obj.Spec.TimeToLive))
				err = r.client.Delete(context.TODO(), &obj) // if failed now, it will be deleted next time
				if err != nil {
					reqLogger.Error(err, fmt.Sprintf("Failed to remove V2VVmware object '%s' after time out, will be scheduled for next round.", obj.Name))
				}
			}
		} else if obj.Labels[vCenterTemporaryLabel] == "true" {
			result = rescheduleResult
			reqLogger.Info(fmt.Sprintf("V2VVMware with '%s' label found, name = '%s'. TimeToLive will be set.", vCenterTemporaryLabel, obj.Name))
			deletionTimeStamp := obj.CreationTimestamp.Time.Add(DefaultTimeToLiveDuration)
			err := r.updateDeletionTimestamp(types.NamespacedName{Name: obj.Name, Namespace: obj.Namespace}, deletionTimeStamp, utils.MaxRetryCount)
			if err != nil {
				reqLogger.Info(fmt.Sprintf("Permanently failed to update timeToLive of '%s' V2VVMWare", obj.Name))
				// ignore and continue with remaining objects
			}
		}
	}

	return result
}


func (r *ReconcileV2VVmware) pruneSecrets(reqLogger logr.Logger, namespace string ) reconcile.Result {
	result := doneResult

	opts := &client.ListOptions{
		Namespace: namespace,
	}

	secrets := &corev1.SecretList{}
	err := r.client.List(context.TODO(), opts, secrets)
	if err != nil {
		reqLogger.Error(err, "Failed to get list of temporary Secret objects.")
		return rescheduleResult
	}

	log.Info(fmt.Sprintf("List of Secret objects retrieved, count: %d", len(secrets.Items)))
	for _, obj := range secrets.Items {
		timeToLiveStr := string(obj.Data["timeToLive"])
		if len(timeToLiveStr) > 0 { // timeToLive is set
			result = rescheduleResult
			reqLogger.Info(fmt.Sprintf("Secret with timeToLive found, name = '%s', value = '%s', now = '%s'", obj.Name, timeToLiveStr, time.Now().Format(time.RFC3339)))
			timeToLive, _ := time.Parse(time.RFC3339, timeToLiveStr)

			if time.Now().After(timeToLive) {
				reqLogger.Info(fmt.Sprintf("Time to live is gone for Secret object '%s', ttl = '%s'. Will be removed", obj.Name, timeToLiveStr))
				err = r.client.Delete(context.TODO(), &obj) // if failed now, it will be deleted next time
				if err != nil {
					reqLogger.Error(err, fmt.Sprintf("Failed to remove Secret object '%s' after time out, will be scheduled for next round.", obj.Name))
				}
			}
		} else if obj.Labels[vCenterTemporaryLabel] == "true" {
			result = rescheduleResult
			reqLogger.Info(fmt.Sprintf("Secret with '%s' label found, name = '%s'. TimeToLive will be set.", vCenterTemporaryLabel, obj.Name))
			deletionTimeStamp := obj.CreationTimestamp.Time.Add(DefaultTimeToLiveDuration)
			err := r.updateSecretDeletionTimestamp(types.NamespacedName{Name: obj.Name, Namespace: obj.Namespace}, deletionTimeStamp, utils.MaxRetryCount)
			if err != nil {
				reqLogger.Info(fmt.Sprintf("Permanently failed to update timeToLive of '%s' Secret", obj.Name))
				// ignore and continue with remaining objects
			}
		}
	}

	return result
}


func (r *ReconcileV2VVmware) Reconcile(request reconcile.Request) (reconcile.Result, error) {
	reqLogger := log.WithValues("Request.Namespace", request.Namespace, "Request.Name", request.Name)
	reqLogger.Info("V2VVmware Garbage Collector")

	resultV2VVMWares := r.pruneV2VVMwares(reqLogger, request.Namespace)
	resultSecrets := r.pruneSecrets(reqLogger, request.Namespace)

	result := resultV2VVMWares
	if result == doneResult {
		result = resultSecrets
	}

	return result, nil // schedule potentially next GC round
}
