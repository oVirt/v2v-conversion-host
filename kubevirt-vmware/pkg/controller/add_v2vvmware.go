package controller

import (
	"github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/v2vvmware"
	gc "github.com/ovirt/v2v-conversion-host/kubevirt-vmware/pkg/controller/garbage_collector"
)

func init() {
	// AddToManagerFuncs is a list of functions to create controllers and add them to a manager.
	AddToManagerFuncs = append(AddToManagerFuncs, v2vvmware.Add)
	AddToManagerFuncs = append(AddToManagerFuncs, gc.GC)
}
