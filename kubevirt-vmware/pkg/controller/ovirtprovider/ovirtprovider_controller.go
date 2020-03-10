package ovirtprovider

import (
	"context"
	"fmt"

	v2vv1alpha1 "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/apis/v2v/v1alpha1"
	"github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/utils"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
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

var log = logf.Log.WithName("controller_ovirtprovider")

// Add creates a new OVirtProvider Controller and adds it to the Manager. The Manager will set fields on the Controller
// and Start it when the Manager is Started.
func Add(mgr manager.Manager) error {
	return add(mgr, newReconciler(mgr))
}

// newReconciler returns a new reconcile.Reconciler
func newReconciler(mgr manager.Manager) reconcile.Reconciler {
	return &ReconcileOVirtProvider{client: mgr.GetClient(), scheme: mgr.GetScheme()}
}

// add adds a new Controller to mgr with r as the reconcile.Reconciler
func add(mgr manager.Manager, r reconcile.Reconciler) error {
	// Create a new controller
	c, err := controller.New("ovirtprovider-controller", mgr, controller.Options{Reconciler: r})
	if err != nil {
		return err
	}

	// Watch for changes to primary resource OVirtProvider
	err = c.Watch(&source.Kind{Type: &v2vv1alpha1.OVirtProvider{}}, &handler.EnqueueRequestForObject{})
	if err != nil {
		return err
	}

	return nil
}

var _ reconcile.Reconciler = &ReconcileOVirtProvider{}

// ReconcileOVirtProvider reconciles a OVirtProvider object
type ReconcileOVirtProvider struct {
	// This client, initialized using mgr.Client() above, is a split client
	// that reads objects from the cache and writes to the apiserver
	client client.Client
	scheme *runtime.Scheme
}

// Reconcile reads that state of the cluster for a OVirtProvider object and makes changes based on the state read
// and what is in the OVirtProvider.Spec
// Note:
// The Controller will requeue the Request to be processed again if the returned error is non-nil or
// Result.Requeue is true, otherwise upon completion it will remove the work from the queue.
func (r *ReconcileOVirtProvider) Reconcile(request reconcile.Request) (reconcile.Result, error) {
	reqLogger := log.WithValues("Request.Namespace", request.Namespace, "Request.Name", request.Name)
	reqLogger.Info("Reconciling OVirtProvider")

	// Fetch the OVirtProvider instance
	instance := &v2vv1alpha1.OVirtProvider{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		if errors.IsNotFound(err) {
			// Request object not found, could have been deleted after reconcile request.
			// Owned objects are automatically garbage collected. For additional cleanup logic use finalizers.
			// Return and don't requeue
			reqLogger.Info("The request object cannot be found.")
			return reconcile.Result{}, nil
		}
		// Error reading the object - requeue the request.
		reqLogger.Info("Error reading the request object, requeuing.")
		return reconcile.Result{}, err
	}

	connectionSecret, err := r.fetchSecret(instance)
	if err != nil {
		reqLogger.Error(err, "Failed to get Secret object for the oVirt connection")
		return reconcile.Result{}, err // request will be re-queued
	}
	reqLogger.Info("Connection secret retrieved.")

	if len(instance.Spec.Vms) == 0 { // list of oVirt VMs is requested to be retrieved
		err = r.readVmsList(request, connectionSecret)
		if err != nil {
			reqLogger.Error(err, "Failed to read list of oVirt VMs.")
			// TODO: check the reason to fail and decide whether to wait like for vmware or to fail the import
		}

		return reconcile.Result{}, err // request will be re-queued if failed
	}

	// secret is present, list of VMs is available, let's check for  details to be retrieved
	var lastError error = nil
	for _, vm := range instance.Spec.Vms { // sequential read is probably good enough, just a single VM or a few of them are expected to be retrieved this way
		if vm.DetailRequest {
			err = r.readVMDetail(request, connectionSecret, vm.Name)
			if err != nil {
				reqLogger.Error(err, fmt.Sprintf("Failed to read '%s' vm details.", vm.Name))
				lastError = err
			}
		}
	}

	return reconcile.Result{}, lastError
}

func (r *ReconcileOVirtProvider) fetchSecret(provider *v2vv1alpha1.OVirtProvider) (*corev1.Secret, error) {
	secret := &corev1.Secret{}
	err := r.client.Get(context.TODO(), types.NamespacedName{Name: provider.Spec.Connection, Namespace: provider.Namespace}, secret)
	return secret, err
}

func getClient(ctx context.Context, secret *corev1.Secret) (*Client, error) {
	return NewClient(ctx, string(secret.Data["apiUrl"]), string(secret.Data["username"]), string(secret.Data["password"]), string(secret.Data["cluster"]))
}

// read whole list at once
func (r *ReconcileOVirtProvider) readVmsList(request reconcile.Request, connectionSecret *corev1.Secret) error {
	log.Info("readVmsList()")

	r.updateStatusPhase(request, v2vv1alpha1.PhaseConnecting)
	client, err := getClient(context.Background(), connectionSecret)
	if err != nil {
		r.updateStatusPhase(request, v2vv1alpha1.PhaseConnectionFailed)
		return err
	}
	defer client.Close()

	r.updateStatusPhase(request, v2vv1alpha1.PhaseLoadingVmsList)
	vms, err := client.GetVMs()
	if err != nil {
		r.updateStatusPhase(request, v2vv1alpha1.PhaseLoadingVmsListFailed)
		return err
	}

	err = r.updateVmsList(request, vms, utils.MaxRetryCount)
	if err != nil {
		r.updateStatusPhase(request, v2vv1alpha1.PhaseLoadingVmsListFailed)
		return err
	}

	r.updateStatusPhase(request, v2vv1alpha1.PhaseConnectionSuccessful)
	return nil
}

func (r *ReconcileOVirtProvider) updateVmsList(request reconcile.Request, vms []string, retryCount int) error {
	instance := &v2vv1alpha1.OVirtProvider{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to get provider object to update list of VMs, intended to write: '%s'", vms))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return r.updateVmsList(request, vms, retryCount-1)
		}
		return err
	}

	instance.Spec.Vms = make([]v2vv1alpha1.OVirtVM, len(vms))
	for index, vmName := range vms {
		instance.Spec.Vms[index] = v2vv1alpha1.OVirtVM{
			Name:          vmName,
			DetailRequest: false, // can be omitted, but just to be clear
		}
	}

	err = r.client.Update(context.TODO(), instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update provider object with list of VMs, intended to write: '%s'", vms))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return r.updateVmsList(request, vms, retryCount-1)
		}
		return err
	}

	return nil
}

func (r *ReconcileOVirtProvider) readVMDetail(request reconcile.Request, connectionSecret *corev1.Secret, vmName string) error {
	log.Info("readVmDetail()")

	r.updateStatusPhase(request, v2vv1alpha1.PhaseConnecting)
	client, err := getClient(context.Background(), connectionSecret)
	if err != nil {
		r.updateStatusPhase(request, v2vv1alpha1.PhaseConnectionFailed)
		return err
	}
	defer client.Close()

	r.updateStatusPhase(request, v2vv1alpha1.PhaseLoadingVMDetail)

	vmDetail, err := client.GetVM(vmName)
	if err != nil {
		r.updateStatusPhase(request, v2vv1alpha1.PhaseLoadingVMDetailFailed)
		return err
	}

	err = r.updateVMDetail(request, vmName, vmDetail, utils.MaxRetryCount)
	if err != nil {
		r.updateStatusPhase(request, v2vv1alpha1.PhaseLoadingVMDetailFailed)
		return err
	}

	r.updateStatusPhase(request, v2vv1alpha1.PhaseConnectionSuccessful)
	return nil
}

func (r *ReconcileOVirtProvider) updateVMDetail(request reconcile.Request, vmName string, vmDetail *v2vv1alpha1.OVirtVMDetail, retryCount int) error {
	instance := &v2vv1alpha1.OVirtProvider{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to get provider object to update detail of '%s' VM.", vmName))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return r.updateVMDetail(request, vmName, vmDetail, retryCount-1)
		}
		return err
	}

	for index, vm := range instance.Spec.Vms {
		if vm.Name == vmName {
			instance.Spec.Vms[index].DetailRequest = false // skip this detail next time
			instance.Spec.Vms[index].Detail = *vmDetail
		}
	}

	err = r.client.Update(context.TODO(), instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update provider object with detail of '%s' VM.", vmName))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			return r.updateVMDetail(request, vmName, vmDetail, retryCount-1)
		}
		return err
	}

	return nil
}

func (r *ReconcileOVirtProvider) updateStatusPhase(request reconcile.Request, phase v2vv1alpha1.VirtualMachineProviderPhase) {
	log.Info(fmt.Sprintf("updateStatusPhase(): %s", phase))
	r.updateStatusPhaseRetry(request, phase, utils.MaxRetryCount)
}

func (r *ReconcileOVirtProvider) updateStatusPhaseRetry(request reconcile.Request, phase v2vv1alpha1.VirtualMachineProviderPhase, retryCount int) {
	// reload instance to workaround issues with parallel writes
	instance := &v2vv1alpha1.OVirtProvider{}
	err := r.client.Get(context.TODO(), request.NamespacedName, instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to get provider object to update status info. Intended to write phase: '%s'", phase))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			r.updateStatusPhaseRetry(request, phase, retryCount-1)
		}
		return
	}

	instance.Status.Phase = phase
	err = r.client.Status().Update(context.TODO(), instance)
	if err != nil {
		log.Error(err, fmt.Sprintf("Failed to update provider status. Intended to write phase: '%s'", phase))
		if retryCount > 0 {
			utils.SleepBeforeRetry()
			r.updateStatusPhaseRetry(request, phase, retryCount-1)
		}
	}
}
