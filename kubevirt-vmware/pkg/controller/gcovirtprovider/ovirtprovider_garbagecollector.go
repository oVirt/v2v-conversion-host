package gcovirtprovider

import (
	"context"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"

	v2vv1alpha1 "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/apis/v2v/v1alpha1"
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

// DefaultTimeToLiveDuration defines default time to live
const DefaultTimeToLiveDuration = time.Hour * 1

var doneResult = reconcile.Result{} // no requeue
var rescheduleResult = reconcile.Result{RequeueAfter: time.Minute * 5}

var log = logf.Log.WithName("gc_ovirtprovider")

// ProviderGC creates a new OVirtProvider Garbage Collector (controller) and adds it to the Manager.
// The Manager will set fields on the Controller and Start it when the Manager is Started.
func ProviderGC(mgr manager.Manager) error {
	return addOvirtProviderGc(mgr, newReconciler(mgr))
}

// newReconciler returns a new reconcile.Reconciler
func newReconciler(mgr manager.Manager) reconcile.Reconciler {
	return &ReconcileOVirtProvider{client: mgr.GetClient(), scheme: mgr.GetScheme()}
}

// add adds a new Controller to mgr with r as the reconcile.Reconciler
func addOvirtProviderGc(mgr manager.Manager, r reconcile.Reconciler) error {
	// Create a new controller
	c, err := controller.New("ovirtprovider-garbage-collector", mgr, controller.Options{Reconciler: r})
	if err != nil {
		return err
	}

	// Watch for changes to primary resource V2VVmware
	err = c.Watch(&source.Kind{Type: &v2vv1alpha1.OVirtProvider{}}, &handler.EnqueueRequestForObject{})
	if err != nil {
		return err
	}

	return nil
}

var _ reconcile.Reconciler = &ReconcileOVirtProvider{}

// ReconcileOVirtProvider reconciles a OVirtProvider object
type ReconcileOVirtProvider struct {
	client client.Client
	scheme *runtime.Scheme
}

func (r *ReconcileOVirtProvider) updateDeletionTimestamp(namespacedName types.NamespacedName, valueTime time.Time, counter int) error {
	value := valueTime.Format(time.RFC3339)
	provider := &v2vv1alpha1.OVirtProvider{}
	err := r.client.Get(context.TODO(), namespacedName, provider) // get a fresh copy
	if err != nil {
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateDeletionTimestamp(namespacedName, valueTime, counter-1)
		}
		return err
	}

	provider.Spec.TimeToLive = value
	err = r.client.Update(context.TODO(), provider)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update provider timeToLive. Intended to write: '%s'", value))
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateDeletionTimestamp(namespacedName, valueTime, counter-1)
		}
	}
	return nil
}

func (r *ReconcileOVirtProvider) updateSecretDeletionTimestamp(namespacedName types.NamespacedName, valueTime time.Time, counter int) error {
	value := valueTime.Format(time.RFC3339)
	secret := &corev1.Secret{}
	err := r.client.Get(context.TODO(), namespacedName, secret) // get a fresh copy
	if err != nil {
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateSecretDeletionTimestamp(namespacedName, valueTime, counter-1)
		}
		return err
	}

	secret.Data["timeToLive"] = []byte(value)
	err = r.client.Update(context.TODO(), secret)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update Secret timeToLive. Intended to write: '%s'", value))
		if counter > 0 {
			utils.SleepBeforeRetry()
			return r.updateSecretDeletionTimestamp(namespacedName, valueTime, counter-1)
		}
	}
	return nil
}

func (r *ReconcileOVirtProvider) prune(reqLogger logr.Logger, namespace string) reconcile.Result {
	result := doneResult

	opts := &client.ListOptions{
		Namespace: namespace,
	}

	providers := &v2vv1alpha1.OVirtProviderList{}
	err := r.client.List(context.TODO(), opts, providers)
	if err != nil {
		reqLogger.Error(err, "Failed to get list of temporary provider objects.")
		return rescheduleResult
	}

	log.Info(fmt.Sprintf("List of providers objects retrieved, count: %d", len(providers.Items)))
	for _, obj := range providers.Items {
		if len(obj.Spec.TimeToLive) > 0 { // timeToLive is set
			result = rescheduleResult
			reqLogger.Info(fmt.Sprintf("Object with timeToLive found, name = '%s', value = '%s', now = '%s'", obj.Name, obj.Spec.TimeToLive, time.Now().Format(time.RFC3339)))
			timeToLive, _ := time.Parse(time.RFC3339, obj.Spec.TimeToLive)

			if time.Now().After(timeToLive) {
				reqLogger.Info(fmt.Sprintf("Time to live is gone for provider object '%s', ttl = '%s'. Will be removed", obj.Name, obj.Spec.TimeToLive))
				err = r.client.Delete(context.TODO(), &obj) // if failed now, it will be deleted next time
				if err != nil {
					reqLogger.Error(err, fmt.Sprintf("Failed to remove provider object '%s' after time out, will be scheduled for next round.", obj.Name))
				}
			}
		}
	}

	return result
}

func (r *ReconcileOVirtProvider) pruneSecrets(reqLogger logr.Logger, namespace string) reconcile.Result {
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
		}
	}

	return result
}

// Reconcile manages how long OVirtProvider should be in the cluster
func (r *ReconcileOVirtProvider) Reconcile(request reconcile.Request) (reconcile.Result, error) {
	reqLogger := log.WithValues("Request.Namespace", request.Namespace, "Request.Name", request.Name)
	reqLogger.Info("Ovirt Garbage Collector")

	resultProvider := r.prune(reqLogger, request.Namespace)
	resultSecrets := r.pruneSecrets(reqLogger, request.Namespace)

	result := resultProvider
	if result == doneResult {
		result = resultSecrets
	}

	return result, nil // schedule potentially next GC round
}
