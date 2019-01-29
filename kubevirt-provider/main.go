package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"

	"github.com/gorilla/mux"
	"github.com/pkliczewski/provider-pod/client"
	"golang.org/x/crypto/ssh"
)

func main() {
	port := getEnv("SERVER_PORT", "8080")
	var router = mux.NewRouter()

	router.HandleFunc("/healthcheck", healthCheck).Methods("GET")
	router.HandleFunc("/vms", GetVMs).Methods("GET")
	router.HandleFunc("/vms/{name}", GetVM).Methods("GET")
	router.HandleFunc("/ssh", GetSshPrint).Methods("POST")
	router.HandleFunc("/sshcheck", GetSshCheck).Methods("GET")

	log.Fatal(http.ListenAndServe(fmt.Sprintf(":%s", port), router))
}

type sshDetails struct {
	Hostname string `json:"host"`
	User     string `json:"username"`
	Password string `json:"password"`
}

type findgerPrint struct {
	Value string
}

func GetSshCheck(w http.ResponseWriter, r *http.Request) {
	var conf sshDetails
	decoder := json.NewDecoder(r.Body)
	if err := decoder.Decode(&conf); err != nil {
		respondWithError(w, http.StatusBadRequest, "Invalid request payload")
		return
	}
	defer r.Body.Close()

	sshConfig := &ssh.ClientConfig{
		User: conf.User,
		Auth: []ssh.AuthMethod{ssh.Password(conf.Password)},
	}
	sshConfig.HostKeyCallback = ssh.InsecureIgnoreHostKey()

	client, err := ssh.Dial("tcp", fmt.Sprintf("%s:22", conf.Hostname), sshConfig)
	if err != nil {
		respondWithJSON(w, http.StatusOK, map[string]string{"result": "false"})
		return
	}

	defer client.Close()

	respondWithJSON(w, http.StatusOK, map[string]string{"result": "true"})
}

func GetSshPrint(w http.ResponseWriter, r *http.Request) {
	var conf sshDetails
	decoder := json.NewDecoder(r.Body)
	if err := decoder.Decode(&conf); err != nil {
		respondWithError(w, http.StatusBadRequest, "Invalid request payload")
		return
	}
	defer r.Body.Close()

	fp := findgerPrint{}
	sshConfig := &ssh.ClientConfig{
		User:            conf.User,
		Auth:            []ssh.AuthMethod{ssh.Password(conf.Password)},
		HostKeyCallback: getHostKey(&fp),
	}

	client, err := ssh.Dial("tcp", fmt.Sprintf("%s:22", conf.Hostname), sshConfig)
	if err != nil {
		log.Println(err)
		respondWithError(w, http.StatusFailedDependency, err.Error())
		return
	}

	defer client.Close()

	respondWithJSON(w, http.StatusOK, map[string]string{"result": fp.Value})
}

func getHostKey(fp *findgerPrint) ssh.HostKeyCallback {
	return func(hostname string, remote net.Addr, key ssh.PublicKey) error {
		fp.Value = ssh.FingerprintLegacyMD5(key)
		// IgnoreHostKey
		return nil
	}
}

func GetVM(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	name := vars["name"]

	if len(name) == 0 {
		respondWithError(w, http.StatusBadRequest, "Invalid virtual machine name")
		return
	}

	ctx := context.Background()

	c, err := client.NewClient(ctx)
	if err != nil {
		log.Println(err)
		respondWithError(w, http.StatusFailedDependency, err.Error())
		return
	}

	defer c.Logout(ctx)

	vm, err := c.GetVM(ctx, name)
	if err != nil {
		log.Println(err)
		respondWithError(w, http.StatusFailedDependency, err.Error())
		return
	}

	respondWithJSON(w, http.StatusOK, map[string]interface{}{"result": vm.Summary.Config})
}

func GetVMs(w http.ResponseWriter, r *http.Request) {
	ctx := context.Background()

	c, err := client.NewClient(ctx)
	if err != nil {
		log.Println(err)
		respondWithError(w, http.StatusFailedDependency, err.Error())
		return
	}

	defer c.Logout(ctx)

	vms, err := c.GetVMs(ctx)
	if err != nil {
		log.Println(err)
		respondWithError(w, http.StatusFailedDependency, err.Error())
		return
	}

	names := make([]string, len(vms))
	for i, vm := range vms {
		names[i] = vm.Summary.Config.Name
	}
	respondWithJSON(w, http.StatusOK, map[string][]string{"result": names})
}

func healthCheck(w http.ResponseWriter, r *http.Request) {
	respondWithJSON(w, http.StatusOK, map[string]string{"result": "OK"})
}

func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}

func respondWithError(w http.ResponseWriter, code int, message string) {
	respondWithJSON(w, code, map[string]string{"error": message})
}

func respondWithJSON(w http.ResponseWriter, code int, payload interface{}) {
	response, _ := json.Marshal(payload)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	w.Write(response)
}
