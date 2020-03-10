package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// OVirtVMDetail contains ovirt vm details as json string
type OVirtVMDetail struct {
	// +optional
	Raw string `json:"raw,omitempty"`
}

// OVirtVM aligns with maintained UI interface
type OVirtVM struct {
	Name string `json:"name"`
	// +optional
	DetailRequest bool `json:"detailRequest,omitempty"` // true if details are requested to be loaded
	// +optional
	Detail OVirtVMDetail `json:"detail,omitempty"`
}

// OVirtProviderSpec defines the desired state of OVirtProvider
type OVirtProviderSpec struct {
	// +optional
	Connection string `json:"connection,omitempty"` // name of Secret with ovirt connection details
	// +optional
	TimeToLive string `json:"timeToLive,omitempty"` // for custom garbage collector
	// +optional
	Vms []OVirtVM `json:"vms,omitempty"`
}

// VirtualMachineProviderPhase defines provider phase
type VirtualMachineProviderPhase string

// We need to keep the same phase details like for vmware
const (
	PhaseConnecting            VirtualMachineProviderPhase = "Connecting"
	PhaseConnectionSuccessful  VirtualMachineProviderPhase = "ConnectionVerified"
	PhaseConnectionFailed      VirtualMachineProviderPhase = "Failed"
	PhaseLoadingVmsList        VirtualMachineProviderPhase = "LoadingVmsList"
	PhaseLoadingVmsListFailed  VirtualMachineProviderPhase = "LoadingVmsList"
	PhaseLoadingVMDetail       VirtualMachineProviderPhase = "LoadingVmDetail"
	PhaseLoadingVMDetailFailed VirtualMachineProviderPhase = "LoadingVmDetailFailed"
)

// OVirtProviderStatus defines the observed state of OVirtProvider
type OVirtProviderStatus struct {
	// +optional
	Phase VirtualMachineProviderPhase `json:"phase,omitempty"` // one of the Phase* constants
}

// +k8s:deepcopy-gen:interfaces=k8s.io/apimachinery/pkg/runtime.Object

// OVirtProvider is the Schema for the ovirtproviders API
// +kubebuilder:subresource:status
// +kubebuilder:resource:path=ovirtproviders,scope=Namespaced
type OVirtProvider struct {
	metav1.TypeMeta `json:",inline"`
	// +optional
	metav1.ObjectMeta `json:"metadata,omitempty"`

	// +optional
	Spec OVirtProviderSpec `json:"spec,omitempty"`
	// +optional
	Status OVirtProviderStatus `json:"status,omitempty"`
}

// +k8s:deepcopy-gen:interfaces=k8s.io/apimachinery/pkg/runtime.Object

// OVirtProviderList contains a list of OVirtProvider
type OVirtProviderList struct {
	metav1.TypeMeta `json:",inline"`
	// +optional
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []OVirtProvider `json:"items"`
}

func init() {
	SchemeBuilder.Register(&OVirtProvider{}, &OVirtProviderList{})
}
