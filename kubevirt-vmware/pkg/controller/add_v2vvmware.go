package controller

import (
	"kubevirt.io/v2v-vmware/pkg/controller/v2vvmware"
)

func init() {
	// AddToManagerFuncs is a list of functions to create controllers and add them to a manager.
	AddToManagerFuncs = append(AddToManagerFuncs, v2vvmware.Add)
	// TODO: register Garbage Collector controller
}
